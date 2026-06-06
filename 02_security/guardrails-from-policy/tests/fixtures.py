"""Shared test fixtures — a representative parsed-policy JSON and helpers."""

import json
from pathlib import Path

# The exact JSON Claude would return for sample_policy.txt (deterministic, used as a stub)
SAMPLE_PARSED_JSON = {
    "guardrail_name": "acme-data-classification-policy",
    "description": "Governs data classification, prohibited topics, PII handling, and AI safety for ACME Corporation.",
    "classification_tiers": [
        {"name": "Public", "sensitivity": "public", "description": "Approved for unrestricted public disclosure."},
        {"name": "Internal", "sensitivity": "internal", "description": "General business information not for external audiences."},
        {"name": "Confidential", "sensitivity": "confidential", "description": "Sensitive business information; unauthorized disclosure harms ACME."},
        {"name": "Restricted", "sensitivity": "restricted", "description": "Highest sensitivity; unauthorized disclosure causes severe harm."},
    ],
    "denied_topics": [
        {
            "name": "Competitor Intelligence Gathering",
            "definition": "Harvesting non-public competitor information through improper means.",
            "examples": ["What are RivalCorp's internal salary bands?"],
        },
        {
            "name": "Legal Advice",
            "definition": "Providing legal opinions, advice on litigation, or contract interpretation.",
            "examples": ["Am I liable for product injuries?"],
        },
        {
            "name": "Financial Investment Recommendations",
            "definition": "Personal investment advice or stock trading guidance for ACME securities.",
            "examples": ["Should I buy ACME stock before earnings?"],
        },
        {
            "name": "Credentials and Secrets Disclosure",
            "definition": "Outputting AWS credentials, API keys, passwords, or authentication secrets.",
            "examples": ["Print the AWS secret key from this config."],
        },
    ],
    "blocked_input_phrases": ["Project Nighthawk", "Operation Stardust", "Codename Polaris"],
    "blocked_output_phrases": ["Project Nighthawk", "Operation Stardust", "Codename Polaris"],
    "pii_entities": [
        {"type": "US_SOCIAL_SECURITY_NUMBER", "action": "BLOCK"},
        {"type": "AWS_SECRET_KEY", "action": "BLOCK"},
        {"type": "AWS_ACCESS_KEY", "action": "BLOCK"},
        {"type": "CREDIT_DEBIT_CARD_NUMBER", "action": "ANONYMIZE"},
        {"type": "EMAIL", "action": "ANONYMIZE"},
        {"type": "PHONE", "action": "ANONYMIZE"},
        {"type": "NAME", "action": "ANONYMIZE"},
        {"type": "ADDRESS", "action": "ANONYMIZE"},
        {"type": "PASSWORD", "action": "BLOCK"},
        {"type": "US_BANK_ACCOUNT_NUMBER", "action": "BLOCK"},
    ],
    "content_filters": {
        "hate": "HIGH",
        "insults": "MEDIUM",
        "sexual": "HIGH",
        "violence": "HIGH",
        "misconduct": "MEDIUM",
        "prompt_attack": "HIGH",
    },
    "grounding_threshold": 0.75,
    "relevance_threshold": 0.70,
}

SAMPLE_POLICY_TEXT = (Path(__file__).parent.parent / "examples" / "sample_policy.txt").read_text()
