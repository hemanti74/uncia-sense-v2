"""User-tunable configuration. Edit values here to change app behavior."""

# Model used for analysis. See MODEL_PRICING in api_client.py for available IDs.
MODEL = "claude-opus-4-7"

# Prompt variant: "fast" (~30-60s) or "full" (~90-120s+, more thorough).
PROMPT_VARIANT = "full"

# Local PDF/image preprocessing with PyMuPDF + OCR before sending to the model.
# Required for DeepSeek (text-only). Set False to A/B-compare against pure vision
# (Anthropic models only).
LOCAL_PREPROCESSING = True

# Maximum total upload size (sum of all selected files) in megabytes.
# Submissions larger than this are rejected before any processing starts to
# protect the server from OOM on huge scans / adversarial inputs.
MAX_TOTAL_UPLOAD_MB = 50
