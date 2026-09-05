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
            percent_matches = list(re.finditer(r"(?<![\w.+-])(\d+(?:\.\d+)?)\s*(?:%|percent\b)", line))

            # Decimal format (0.948)
            decimal_matches = re.finditer(r"(?<![\w.+-])0\.\d+\b", line)
            decimals = [m.group() for m in decimal_matches
                        if not any(p.start() <= m.start() < p.end() for p in percent_matches)]

            for p in percent_matches:
                if 0 <= float(p[1]) <= 100:
                    candidates.append(p[1] + "%")

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
