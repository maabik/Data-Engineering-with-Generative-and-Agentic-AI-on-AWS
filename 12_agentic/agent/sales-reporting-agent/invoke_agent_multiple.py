#!/usr/bin/env python3

import asyncio
from sales_reporting_agent import create_sales_agent

async def invoke_agent(query: str, iteration: int):
    """Invoke agent with a query"""
    print(f"\n{'='*80}")
    print(f"Invocation #{iteration}: {query}")
    print(f"{'='*80}")
    
    agent = create_sales_agent()
    response = agent(query)
    result = response.message["content"][0]["text"]
    
    print(f"\nResponse:\n{result}\n")
    return result

async def main():
    queries = [
        "What are the top 5 products by sales in Q4 2023?",
        "Show me sales performance by region",
        "What's the total revenue for 2023?",
        "Which customers have the highest order values?",
        "Show me monthly sales trends",
    ]
    
    for i, query in enumerate(queries, 1):
        await invoke_agent(query, i)
        await asyncio.sleep(2)  # Brief pause between invocations

if __name__ == "__main__":
    asyncio.run(main())
