def _chunk_list(ids, chunk_size=50):
    """Splits the list of IDs into smaller batches."""
    for i in range(0, len(ids), chunk_size):
        yield ids[i:i + chunk_size]

def _sync_lists_diff(...):
    ...
    # Before executing DELETE queries
    ids_to_delete = [...]  # Assume this is populated as needed
    for chunk in _chunk_list(ids_to_delete):
        placeholders = ','.join(['?'] * len(chunk))
        query = f"DELETE FROM your_table WHERE id IN ({placeholders})"
        self.db.execute(query, chunk)
    ...
