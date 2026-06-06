#!/usr/bin/env python3
from sales_reporting_agent import create_sales_agent

agent = create_sales_agent()
response = agent("What are the top 5 products by sales in Q4 2023?")
print(response.message["content"][0]["text"])
