"""Batch processor with a deliberately late-discovered cursor bug."""


def process_batches(batches):
    result = []
    cursor = 0
    for batch in batches:
        result.extend(batch[cursor:])
        cursor = len(batch)
    return result
