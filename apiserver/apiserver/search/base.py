TOP_K_SIZE = 50


class ClientError(ValueError):
    """Error in query sent by client.
    """


def get_column_identifiers(es, column_names, dataset_id=None, data_profile=None):
    column_indices = [-1 for _ in column_names]
    if not data_profile:
        columns = es.get('datamart', dataset_id, _source='columns.name')
        columns = columns['_source']['columns']
    else:
        columns = data_profile['columns']
    for i in range(len(columns)):
        for j in range(len(column_names)):
            if columns[i]['name'] == column_names[j]:
                column_indices[j] = i
    return column_indices
