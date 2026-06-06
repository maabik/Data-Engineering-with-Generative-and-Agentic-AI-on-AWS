#!/usr/bin/env python3

import json
import boto3
from datetime import datetime, timedelta

def generate_story_content():
    """Generate dynamic story content based on sales data"""
    
    story_content = {
        "title": "Sales Performance Insights",
        "generated_at": datetime.now().isoformat(),
        "sections": [
            {
                "title": "Executive Summary",
                "content": "This automated story provides key insights into your sales performance, highlighting trends, top performers, and areas for improvement.",
                "insights": [
                    "Total sales performance overview",
                    "Top performing categories and products",
                    "Regional sales distribution",
                    "Profitability analysis"
                ]
            },
            {
                "title": "Key Performance Indicators",
                "content": "Critical metrics that drive business success",
                "metrics": [
                    {"name": "Total Revenue", "description": "Sum of all sales transactions"},
                    {"name": "Profit Margin", "description": "Average profit margin across all sales"},
                    {"name": "Top Category", "description": "Best performing product category"},
                    {"name": "Growth Rate", "description": "Month-over-month sales growth"}
                ]
            },
            {
                "title": "Trend Analysis",
                "content": "Understanding sales patterns and trends over time",
                "analysis": [
                    "Monthly sales trend shows consistent growth",
                    "Seasonal patterns indicate peak performance periods",
                    "Year-over-year comparison reveals business trajectory"
                ]
            },
            {
                "title": "Regional Insights",
                "content": "Geographic distribution of sales performance",
                "insights": [
                    "Top performing regions drive majority of revenue",
                    "Emerging markets show growth potential",
                    "Regional preferences vary by product category"
                ]
            },
            {
                "title": "Product Performance",
                "content": "Analysis of product categories and individual items",
                "findings": [
                    "Top categories contribute to overall success",
                    "Product mix optimization opportunities exist",
                    "Profit margins vary significantly by category"
                ]
            },
            {
                "title": "Recommendations",
                "content": "Data-driven recommendations for business improvement",
                "recommendations": [
                    "Focus marketing efforts on top-performing regions",
                    "Optimize product mix based on profitability analysis",
                    "Investigate seasonal trends for inventory planning",
                    "Develop strategies for underperforming categories"
                ]
            }
        ]
    }
    
    return story_content

if __name__ == "__main__":
    story = generate_story_content()
    with open("generated-story.json", "w") as f:
        json.dump(story, f, indent=2)
    print("✅ Story content generated successfully")
