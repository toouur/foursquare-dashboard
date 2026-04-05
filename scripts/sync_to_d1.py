def _sync_lists_diff(list_a, list_b):
    to_delete = [item for item in list_a if item not in list_b]
    to_add = [item for item in list_b if item not in list_a]

    # Batch deletion to avoid too many SQL variables error
    for item in to_delete:
        delete_item(item)

    # Assuming delete_item and add_item handle the individual deletions and additions
    for item in to_add:
        add_item(item)

    # Additional code that exists in the original function...
    # ...
