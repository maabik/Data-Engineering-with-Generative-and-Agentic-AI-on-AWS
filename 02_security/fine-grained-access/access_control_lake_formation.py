import boto3
client = boto3.client('lakeformation')

#grant access
response = client.batch_grant_permissions(
    CatalogId='string',
    Entries=[
        {
            'Id': 'string',
            'Principal': {
                'DataLakePrincipalIdentifier': 'arn:aws:iam::123456789012:user/user-employee-data-access'  # Replace with the actual IAM user ARN
            },
            'Resource': {
                'Table': {
                    'CatalogId': 'string',
                    'DatabaseName': 'employee_database',
                    'Name': 'salaries',
                    'TableWithColumns': {
                        'CatalogId': 'string',
                        'DatabaseName': 'employee_database',
                        'Name': 'salaries',
                        'ColumnNames': [
                            'salary',
                            'from_date'
                        ],
                        'ColumnWildcard': {}
                    }
                }
            },
            'Permissions': [
                'SELECT'
            ],
            'PermissionsWithGrantOption': [
                'SELECT'
            ]
        },
    ]
)

#revoke access
response = client.batch_revoke_permissions(
    CatalogId='string',
    Entries=[
        {
            'Id': 'string',
            'Principal': {
                'DataLakePrincipalIdentifier': 'arn:aws:iam::123456789012:user/employee-data-access-user'  # Replace with the actual IAM user ARN
            },
            'Resource': {
                'Table': {
                    'CatalogId': 'string',
                    'DatabaseName': 'employee_database',
                    'Name': 'salaries',
                    'TableWithColumns': {
                        'CatalogId': 'string',
                        'DatabaseName': 'employee_database',
                        'Name': 'salaries',
                        'ColumnNames': [
                            'salary',
                            'from_date'
                        ],
                        'ColumnWildcard': {}
                    }
                }
            }
        },
    ]
)

