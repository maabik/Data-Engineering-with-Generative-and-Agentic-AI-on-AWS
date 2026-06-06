#!/usr/bin/env python3
from sales_reporting_agent import create_sales_agent

queries = [
    "What are the top 5 products by sales in Q4 2023?",
    "Show me sales performance by region",
    "What's the total revenue for 2023?",
]

for i, query in enumerate(queries, 1):
    print(f"\n{'='*80}\nInvocation #{i}: {query}\n{'='*80}")
    agent = create_sales_agent()
    response = agent(query)
    print(response.message["content"][0]["text"])
