def transform(values, scale):
    """Scale each numeric input for downstream processing."""
    return [value * scale for value in values]


def summarize(values):
    return sum(values)
