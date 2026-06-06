# multimodal-enrichment

Side-by-side notebooks comparing AWS purpose-built AI services
(**Amazon Rekognition**, **Amazon Textract**) against a multimodal
foundation model (**Anthropic Claude Sonnet 4.6 on Amazon Bedrock**) for
two enrichment workloads:

| Notebook | Workload | AWS service | FM |
| --- | --- | --- | --- |
| `image_analysis.ipynb` | Image enrichment (labels, image properties, faces, celebrities, text-in-image, PPE) | Rekognition | Claude on Bedrock |
| `Intelligent_Document_Processing.ipynb` | IDP (vaccination card, expense receipt, drivers license) | Textract (`AnalyzeDocument`, `AnalyzeExpense`, `AnalyzeID`) | Claude on Bedrock |

Each section runs both providers against the same input, prints latency,
and shows the raw output so you can compare accuracy/effort trade-offs.
The IDP notebook also has a final section that **augments the Claude
prompt with Textract output** to demonstrate hybrid pipelines.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

You also need:

- AWS credentials (`aws configure`) with permission for `bedrock:InvokeModel`,
  `rekognition:*` (the calls used in this notebook), and `textract:*`.
- Bedrock model access for **Claude Sonnet 4.6**
  (inference profile id `us.anthropic.claude-sonnet-4-6`) granted in your
  account via the Bedrock console.
- An `images/` directory next to the notebooks containing the sample
  files referenced inline (e.g. `images/label_detection/skateboard_resized.jpg`,
  `images/analyze doc/vaccination_card.png`, etc.). These ship with the
  Apress book that this folder accompanies; copy them in before running.

## Running

```bash
jupyter lab
```

Open either notebook and execute top-to-bottom. Each cell is independent
of the next *within a topic*, so you can re-run individual sections.

## What was fixed in this version

- `save_table_as_csv` is now called **inside** the page/table loop —
  previously only the last table was ever written.
- `print_rekognition_celeb_details` no longer `return`s after the first
  celebrity — multi-celeb images now print all matches.
- `claude_response` derives `media_type` from the file extension; PNG
  inputs (`vaccination_card.png`, `DLRegular.png`) used to be sent with a
  hardcoded `image/jpeg` content type, which is invalid for the
  Bedrock messages API.
- Model bumped from Claude 3.5 Sonnet (2024-06-20) to Claude Sonnet 4.6
  via the `us.anthropic.claude-sonnet-4-6` inference profile.
- `MODEL_ID` and `MAX_TOKENS` hoisted to module-level constants.
- Prompt typos corrected (`confodent` → `confident`, `seperately` →
  `separately`, `yourslf` → `yourself`).
