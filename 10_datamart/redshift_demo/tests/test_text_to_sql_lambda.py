"""Tests for scripts/text_to_sql_lambda.py.

The lambda module instantiates boto3 clients at import time. We patch
boto3.client and boto3.resource before importing it so all clients are
MagicMocks that we can configure per-test.
"""
import importlib
import json
import os
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def lambda_module(monkeypatch):
    """Import (or reimport) text_to_sql_lambda with mocked boto3 + clean env."""
    # Required env so the agent path is taken when configured.
    monkeypatch.setenv('PROMPT_TEMPLATE_TABLE', 'TestTemplates')
    monkeypatch.setenv('PROMPT_TEMPLATE_ID', 'test-template')
    monkeypatch.setenv('REDSHIFT_SECRET_ARN', 'arn:aws:secretsmanager:us-east-1:111111111111:secret:test')
    monkeypatch.setenv('TEXT_TO_SQL_AGENT_ID', 'AGENT123')
    monkeypatch.setenv('TEXT_TO_SQL_AGENT_ALIAS_ID', 'ALIAS456')

    # Patch boto3 BEFORE importing the module.
    import boto3
    fake_clients = {
        'bedrock-runtime': MagicMock(name='bedrock'),
        'bedrock-agent-runtime': MagicMock(name='bedrock_agent'),
        'secretsmanager': MagicMock(name='secrets_manager'),
        'redshift-data': MagicMock(name='redshift_data'),
    }
    fake_resource = MagicMock(name='dynamodb')

    monkeypatch.setattr(boto3, 'client', lambda name, **_: fake_clients[name])
    monkeypatch.setattr(boto3, 'resource', lambda name, **_: fake_resource)

    # Force a fresh import so module-level clients are the patched ones.
    sys.modules.pop('text_to_sql_lambda', None)
    module = importlib.import_module('text_to_sql_lambda')

    # Expose the mocks on the module for assertions.
    module._fakes = fake_clients
    module._fake_dynamodb = fake_resource
    return module


def _api_event(body):
    return {'body': json.dumps(body)}


class TestRequestValidation:
    def test_missing_body_returns_400(self, lambda_module):
        result = lambda_module.lambda_handler({}, None)
        assert result['statusCode'] == 400
        assert 'Missing request body' in json.loads(result['body'])['error']

    def test_missing_query_returns_400(self, lambda_module):
        result = lambda_module.lambda_handler(_api_event({}), None)
        assert result['statusCode'] == 400
        assert "Missing 'query' parameter" in json.loads(result['body'])['error']

    def test_response_includes_cors_headers(self, lambda_module):
        result = lambda_module.lambda_handler({}, None)
        assert result['headers']['Access-Control-Allow-Origin'] == '*'


class TestStripSqlFences:
    def test_handles_sql_fences(self, lambda_module):
        text = "```sql\nSELECT 1;\n```"
        assert lambda_module.strip_sql_fences(text) == 'SELECT 1;'

    def test_handles_plain_fences(self, lambda_module):
        text = "```\nSELECT 1;\n```"
        assert lambda_module.strip_sql_fences(text) == 'SELECT 1;'

    def test_passes_through_unfenced(self, lambda_module):
        assert lambda_module.strip_sql_fences('SELECT 1;') == 'SELECT 1;'


class TestGenerateSqlWithAgent:
    def test_aggregates_streamed_chunks(self, lambda_module):
        agent = lambda_module._fakes['bedrock-agent-runtime']
        agent.invoke_agent.return_value = {
            'completion': [
                {'chunk': {'bytes': b'SELECT * '}},
                {'chunk': {'bytes': b'FROM sales.fact_sales;'}},
            ]
        }
        sql = lambda_module.generate_sql_with_agent('show all sales', 'sess-1')
        assert sql == 'SELECT * FROM sales.fact_sales;'
        agent.invoke_agent.assert_called_once()
        kwargs = agent.invoke_agent.call_args.kwargs
        assert kwargs['agentId'] == 'AGENT123'
        assert kwargs['agentAliasId'] == 'ALIAS456'
        assert kwargs['sessionId'] == 'sess-1'

    def test_strips_fences_from_streamed_output(self, lambda_module):
        agent = lambda_module._fakes['bedrock-agent-runtime']
        agent.invoke_agent.return_value = {
            'completion': [{'chunk': {'bytes': b'```sql\nSELECT 1;\n```'}}]
        }
        assert lambda_module.generate_sql_with_agent('q', 's') == 'SELECT 1;'

    def test_raises_when_agent_not_configured(self, lambda_module, monkeypatch):
        monkeypatch.setattr(lambda_module, 'TEXT_TO_SQL_AGENT_ID', None)
        with pytest.raises(RuntimeError, match='not configured'):
            lambda_module.generate_sql_with_agent('q', 's')


class TestGenerateSqlWithBedrock:
    def test_uses_messages_api_format(self, lambda_module):
        bedrock = lambda_module._fakes['bedrock-runtime']
        bedrock.invoke_model.return_value = {
            'body': MagicMock(read=lambda: json.dumps({
                'content': [{'type': 'text', 'text': 'SELECT 1;'}],
            }).encode())
        }

        sql = lambda_module.generate_sql_with_bedrock('q', 'TEMPLATE {user_query}')
        assert sql == 'SELECT 1;'

        body = json.loads(bedrock.invoke_model.call_args.kwargs['body'])
        # Must use Messages API anthropic_version, not legacy completions.
        assert body['anthropic_version'] == 'bedrock-2023-05-31'
        assert body['messages'][0]['role'] == 'user'
        assert 'prompt' not in body
        assert 'max_tokens_to_sample' not in body

    def test_uses_sonnet_46_inference_profile_by_default(self, lambda_module):
        bedrock = lambda_module._fakes['bedrock-runtime']
        bedrock.invoke_model.return_value = {
            'body': MagicMock(read=lambda: json.dumps({'content': [{'type': 'text', 'text': 'X'}]}).encode())
        }
        lambda_module.generate_sql_with_bedrock('q', 't')
        model_id = bedrock.invoke_model.call_args.kwargs['modelId']
        assert 'claude-sonnet-4-6' in model_id


class TestLambdaHandlerHappyPath:
    def test_full_flow_uses_agent_then_executes_sql(self, lambda_module):
        agent = lambda_module._fakes['bedrock-agent-runtime']
        agent.invoke_agent.return_value = {
            'completion': [{'chunk': {'bytes': b'SELECT 1;'}}]
        }

        secrets = lambda_module._fakes['secretsmanager']
        secrets.get_secret_value.return_value = {
            'SecretString': json.dumps({
                'cluster_identifier': 'demo', 'database': 'salesdb', 'username': 'admin',
            })
        }

        rs = lambda_module._fakes['redshift-data']
        rs.execute_statement.return_value = {'Id': 'qid'}
        rs.describe_statement.return_value = {'Status': 'FINISHED'}
        rs.get_statement_result.return_value = {
            'ColumnMetadata': [{'name': 'col1'}],
            'Records': [[{'longValue': 42}]],
        }

        result = lambda_module.lambda_handler(_api_event({'query': 'pick one'}), None)

        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert body['sql'] == 'SELECT 1;'
        assert body['results'] == {'columns': ['col1'], 'rows': [{'col1': 42}]}

    def test_falls_back_to_bedrock_when_agent_fails(self, lambda_module):
        agent = lambda_module._fakes['bedrock-agent-runtime']
        agent.invoke_agent.side_effect = RuntimeError("agent down")

        # Fallback path needs the prompt template from DynamoDB.
        table = MagicMock()
        table.get_item.return_value = {'Item': {'template': 'T {user_query}'}}
        lambda_module._fake_dynamodb.Table.return_value = table

        bedrock = lambda_module._fakes['bedrock-runtime']
        bedrock.invoke_model.return_value = {
            'body': MagicMock(read=lambda: json.dumps({
                'content': [{'type': 'text', 'text': 'SELECT 2;'}],
            }).encode())
        }

        secrets = lambda_module._fakes['secretsmanager']
        secrets.get_secret_value.return_value = {
            'SecretString': json.dumps({
                'cluster_identifier': 'demo', 'database': 'salesdb', 'username': 'admin',
            })
        }

        rs = lambda_module._fakes['redshift-data']
        rs.execute_statement.return_value = {'Id': 'qid'}
        rs.describe_statement.return_value = {'Status': 'FINISHED'}
        rs.get_statement_result.return_value = {
            'ColumnMetadata': [{'name': 'n'}], 'Records': [[{'longValue': 2}]],
        }

        result = lambda_module.lambda_handler(_api_event({'query': 'pick two'}), None)
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert body['sql'] == 'SELECT 2;'

    def test_failed_query_returns_500(self, lambda_module):
        agent = lambda_module._fakes['bedrock-agent-runtime']
        agent.invoke_agent.return_value = {
            'completion': [{'chunk': {'bytes': b'BAD SQL'}}]
        }
        secrets = lambda_module._fakes['secretsmanager']
        secrets.get_secret_value.return_value = {
            'SecretString': json.dumps({
                'cluster_identifier': 'demo', 'database': 'salesdb', 'username': 'admin',
            })
        }
        rs = lambda_module._fakes['redshift-data']
        rs.execute_statement.return_value = {'Id': 'qid'}
        rs.describe_statement.return_value = {'Status': 'FAILED', 'Error': 'syntax error'}

        result = lambda_module.lambda_handler(_api_event({'query': 'broken'}), None)
        assert result['statusCode'] == 500
        body = json.loads(result['body'])
        assert 'syntax error' in body['error']


class TestSessionIdHandling:
    def test_uses_caller_session_id_when_provided(self, lambda_module):
        agent = lambda_module._fakes['bedrock-agent-runtime']
        agent.invoke_agent.return_value = {
            'completion': [{'chunk': {'bytes': b'SELECT 1;'}}]
        }
        secrets = lambda_module._fakes['secretsmanager']
        secrets.get_secret_value.return_value = {
            'SecretString': json.dumps({
                'cluster_identifier': 'demo', 'database': 'salesdb', 'username': 'admin',
            })
        }
        rs = lambda_module._fakes['redshift-data']
        rs.execute_statement.return_value = {'Id': 'qid'}
        rs.describe_statement.return_value = {'Status': 'FINISHED'}
        rs.get_statement_result.return_value = {
            'ColumnMetadata': [{'name': 'n'}], 'Records': [[{'longValue': 1}]],
        }

        lambda_module.lambda_handler(
            _api_event({'query': 'q', 'sessionId': 'caller-supplied-session'}),
            None,
        )

        kwargs = agent.invoke_agent.call_args.kwargs
        assert kwargs['sessionId'] == 'caller-supplied-session'
