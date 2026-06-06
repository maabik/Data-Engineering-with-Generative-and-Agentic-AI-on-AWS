#!/usr/bin/env python3
"""Redshift MCP Server for AgentCore Runtime deployment."""

from mcp.server.fastmcp import FastMCP
from awslabs.redshift_mcp_server.server import (
    list_clusters_tool,
    list_databases_tool,
    list_schemas_tool,
    list_tables_tool,
    list_columns_tool,
    execute_query_tool
)

# Create FastMCP instance with stateless_http=True for AgentCore compatibility
mcp = FastMCP(
    name="awslabs.redshift-mcp-server",
    host="0.0.0.0",
    stateless_http=True,
    instructions="""
# Amazon Redshift MCP Server for AgentCore Runtime

This MCP server provides comprehensive access to Amazon Redshift clusters and serverless workgroups.

## Available Tools

### list_clusters
Lists all available Redshift clusters and serverless workgroups in your AWS account.

### list_databases
Lists all databases in a specified Redshift cluster.

### list_schemas
Lists all schemas in a specified database within a Redshift cluster.

### list_tables
Lists all tables in a specified schema within a Redshift database.

### list_columns
Lists all columns in a specified table within a Redshift schema.

### execute_query
Executes SQL queries against a Redshift cluster or serverless workgroup.

## Getting Started

1. Ensure your AWS configuration and credentials are configured.
2. Use the list_clusters tool to discover available Redshift instances.
3. Note the cluster identifiers for use with other tools.
"""
)

# Register all the tools from the original server
mcp.tool(name='list_clusters')(list_clusters_tool)
mcp.tool(name='list_databases')(list_databases_tool)
mcp.tool(name='list_schemas')(list_schemas_tool)
mcp.tool(name='list_tables')(list_tables_tool)
mcp.tool(name='list_columns')(list_columns_tool)
mcp.tool(name='execute_query')(execute_query_tool)

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
