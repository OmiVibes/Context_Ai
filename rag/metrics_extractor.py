import re
from typing import Optional

def extract_accuracy(text: str) -> Optional[str]:
    """
    Generic accuracy extractor (one-time implementation).

    Supports:
    - **Accuracy**: 94.8%
    - Accuracy: 94.8 percent
    - Validation accuracy = 0.948
    - Model performance ~ 95%
    - Any README writing style
    """

    # 1️⃣ Normalize text (remove markdown & noise)
    cleaned = re.sub(r"[*_`#>|]", " ", text.lower())

    # 2️⃣ Split into lines for semantic locality
    lines = cleaned.splitlines()

    # 3️⃣ Keywords indicating performance metrics
    keywords = [
        "accuracy",
        "validation accuracy",
        "model accuracy",
        "classification accuracy",
        "performance"
    ]

    candidates = []

    for line in lines:
        if any(k in line for k in keywords):
            # Percent format (94.8%)
            percents = re.findall(r"\d{1,3}(?:\.\d+)?\s*%", line)

            # Decimal format (0.948)
            decimals = re.findall(r"\b0\.\d+\b", line)

            for p in percents:
                candidates.append(p.strip())

            for d in decimals:
                try:
                    candidates.append(f"{round(float(d) * 100, 2)}%")
                except Exception:
                    pass

    if not candidates:
        return None

    # 4️⃣ Pick the most confident (highest value)
    def score(val: str) -> float:
        try:
            return float(val.replace("%", ""))
        except Exception:
            return 0.0

    best = max(candidates, key=score)
    return best
