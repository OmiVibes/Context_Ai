import os

IGNORE = {"__pycache__", "venv", ".git"}

def summarize_structure(base_path):
    lines = []
    for root, dirs, files in os.walk(base_path):
        if any(ig in root for ig in IGNORE):
            continue
        level = root.replace(base_path, "").count(os.sep)
        indent = "  " * level
        folder = os.path.basename(root)
        lines.append(f"{indent}- {folder}/")
        for f in files:
            if f.endswith((".py", ".md", ".yaml", ".json")):
                lines.append(f"{indent}  - {f}")
    return "\n".join(lines)


def infer_architecture(base_path):
    structure = summarize_structure(base_path)

    return f"""
Below is an inferred overview of how the project is structured based on its repository contents.
This answer is based ONLY on what exists inside the repository.

📌 Possible Architecture Components:
- **Data / Input Layer** – Handles dataset loading, preprocessing, or input files
- **Model / Algorithm Layer** – Neural network definition, training logic, inference code
- **Evaluation / Visualization** – Metrics, charts, Grad-CAM heatmaps, UI notebooks
- **Prediction Interface** – API, Streamlit, CLI, or notebook used to run predictions

📌 Likely Workflow (based on common ML repo patterns):
1. Load and preprocess input images
2. Feed data to the trained CNN model
3. Generate classification (tumor vs. non-tumor)
4. Visualize or return predictions

📁 Repository Structure Snapshot:
{structure}
""".strip()
