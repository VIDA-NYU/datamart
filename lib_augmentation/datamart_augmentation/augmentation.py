import itertools
import logging
import numpy as np
import pandas as pd
from sklearn.neighbors._kd_tree import KDTree
import time

from datamart_materialize import types
from datamart_profiler.spatial import median_smallest_distance
from datamart_profiler.temporal import get_temporal_resolution, \
    temporal_aggregation_keys


logger = logging.getLogger(__name__)


class AugmentationError(ValueError):
    """Error during augmentation.
    """


class WriteCounter(object):
    """File wrapper that counts the number of bytes written.
    """
    def __init__(self, inner):
        self.inner = inner
        self.size = 0

    def __iter__(self):
        # Pandas needs file objects to have __iter__
        return self

    def write(self, buf):
        self.size += len(buf)
        return self.inner.write(buf)

    def flush(self):
        return self.inner.flush()

    def close(self):
        self.inner.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self.inner.__exit__(exc_type, exc_val, exc_tb)


class _UniqueIndexKey(object):
    def __repr__(self):
        return "UNIQUE_INDEX_KEY"


UNIQUE_INDEX_KEY = _UniqueIndexKey()


temporal_resolutions_priorities = {
    n: i
    for i, n in enumerate(reversed(list(temporal_aggregation_keys)))
}


def _transform_index(level, func):
    def inner(index):
        if isinstance(index, pd.MultiIndex):
            old_index = index.to_frame()
            return pd.MultiIndex.from_arrays(
                [old_index.iloc[:, i] if i != level
                 else func(old_index.iloc[:, i])
                 for i in range(len(index.levels))]
            )
        elif isinstance(index, pd.Series):
            return func(pd.Index(index))
        else:
            return func(index)

    return inner


def _transform_data_index(data, level, func):
    if isinstance(data.index, pd.MultiIndex):
        old_index = data.index.to_frame()
        data.index = pd.MultiIndex.from_arrays(
            [old_index.iloc[:, i] if i != level
             else func(old_index.iloc[:, i])
             for i in range(len(data.index.levels))]
        )
    else:
        data.index = func(data.index)


def set_data_index(data, columns, columns_metadata, drop=False):
    """
    Converts columns in a dataset (pandas.DataFrame) to their corresponding
    data types, based on the provided metadata.
    """

    data.set_index(
        [columns_metadata[column]['name'] for column in columns],
        drop=drop,
        inplace=True
    )

    for i, col_idx in enumerate(columns):
        column = columns_metadata[col_idx]
        if types.DATE_TIME in column['semantic_types']:
            _transform_data_index(
                data, i,
                lambda idx: pd.to_datetime(idx.map(str), errors='coerce'),
            )
        elif column['structural_type'] == types.INTEGER:
            _transform_data_index(
                data, i,
                lambda idx: pd.to_numeric(idx, errors='coerce', downcast='integer'),
            )
        elif column['structural_type'] == types.FLOAT:
            _transform_data_index(
                data, i,
                lambda idx: pd.to_numeric(idx, errors='coerce', downcast='float'),
            )
        elif column['structural_type'] == types.TEXT:
            _transform_data_index(
                data, i,
                lambda idx: idx.str.lower(),
            )

    # Names of multiindex have to match for join() to work
    data.index.names = ['%04d' % i for i in range(len(data.index.names))]

    return data


def match_temporal_resolutions(input_data, companion_data, temporal_resolution=None):
    """Matches the resolutions between the datasets.

    This takes in example indexes, and returns a function to update future
    indexes. This is because we are streaming, and want to decide once how to
    process multiple batches.
    """

    if isinstance(input_data.index, pd.MultiIndex):
        # Find which levels are temporal
        funcs = []
        for i, lvl in enumerate(input_data.index.levels):
            if isinstance(lvl, pd.DatetimeIndex):
                funcs.append(
                    match_column_temporal_resolutions(
                        input_data.index,
                        companion_data.index,
                        i,
                        temporal_resolution,
                    )
                )
            else:
                funcs.append(lambda x: x)

        def transform(index):
            old_index = index.to_frame()
            return pd.MultiIndex.from_arrays(
                func(old_index.iloc[:, lvl])
                for lvl, func in zip(range(len(index.levels)), funcs)
            )

        return transform
    elif (isinstance(input_data.index, pd.DatetimeIndex)
          and isinstance(companion_data.index, pd.DatetimeIndex)):
        return match_column_temporal_resolutions(
            input_data.index,
            companion_data.index,
            0,
            temporal_resolution,
        )

    return lambda idx: idx  # no-op


def match_column_temporal_resolutions(index_1, index_2, level,
                                      temporal_resolution=None):
    """Matches the resolutions between the dataset indices.
    """

    col = ''
    if isinstance(index_1, pd.MultiIndex):
        index_1 = index_1.levels[level]
        index_2 = index_2.levels[level]
        col = " (level %d)" % level

    if not (index_1.is_all_dates and index_2.is_all_dates):
        return lambda idx: idx

    # Keep in sync with search.get_joinable_datasets()

    # Use the provided resolution
    if temporal_resolution is not None:
        key = temporal_aggregation_keys[temporal_resolution]
        logger.info("Temporal alignment: requested '%s'", temporal_resolution)
        if isinstance(key, str):
            return _transform_index(level, lambda idx: idx.strftime(key))
        else:
            return _transform_index(level, lambda idx: idx.map(key))
    else:
        # Pick the more coarse of the two resolutions
        resolution_1 = get_temporal_resolution(index_1[~index_1.isna()])
        resolution_2 = get_temporal_resolution(index_2[~index_2.isna()])

        if (temporal_resolutions_priorities[resolution_1] >
                temporal_resolutions_priorities[resolution_2]):
            # Change resolution of second index to the first's
            logger.info(
                "Temporal alignment: right to '%s'%s",
                resolution_1,
                col,
            )
            key = temporal_aggregation_keys[resolution_1]
            if isinstance(key, str):
                return _transform_index(level, lambda idx: idx.strftime(key))
            else:
                return _transform_index(level, lambda idx: idx.map(key))
        else:
            # Change resolution of first index to the second's
            logger.info(
                "Temporal alignment: left to '%s'%s",
                resolution_2,
                col,
            )
            key = temporal_aggregation_keys[resolution_2]
            if isinstance(key, str):
                return _transform_index(level, lambda idx: idx.strftime(key))
            else:
                return _transform_index(level, lambda idx: idx.map(key))


def _first(series):
    return series.iloc[0]


def _sum(series):
    """Variant of numpy.sum() that returns nan for all-nan array.

    That way it works similarly to numpy.mean(), numpy.max(), etc instead of
    returning 0.
    """
    if np.any(~np.isnan(series)):
        return np.sum(series)
    else:
        return np.nan


AGGREGATION_FUNCTIONS = {
    'first': pd.NamedAgg('first', _first),
    'mean': pd.NamedAgg('mean', np.mean),
    'sum': pd.NamedAgg('sum', _sum),
    'max': pd.NamedAgg('max', np.max),
    'min': pd.NamedAgg('min', np.min),
    'count': pd.NamedAgg('count', lambda s: (~s.isna()).sum()),
}


def perform_aggregations(
    data, original_columns,
    agg_functions=None, augment_columns_name=None,
):
    """Performs group by on dataset after join, to keep the shape of the
    new, augmented dataset the same as the original, input data.
    """

    col_indices = {
        col: idx for idx, col in enumerate(data.columns)
    }

    start = time.perf_counter()
    original_columns_set = set(original_columns)

    provided_agg_functions = agg_functions
    if provided_agg_functions:
        provided_agg_functions = {
            # Columns might have been renamed if conflicting, deal with that
            augment_columns_name[col]:
                # Turn single value into list
                [funcs] if isinstance(funcs, str) else funcs
            for col, funcs in provided_agg_functions.items()
        }

    agg_functions = dict()
    for column in data.columns:
        if column == UNIQUE_INDEX_KEY or column in original_columns_set:
            # Just pick the first value
            # (they are all the same, from a single row in the original data)
            agg_functions[column] = ['first']
        elif provided_agg_functions:
            try:
                funcs = provided_agg_functions[column]
            except KeyError:
                pass
            else:
                agg_functions[column] = (
                    [funcs] if isinstance(funcs, str)
                    else funcs
                )
        else:
            if ('int' in str(data.dtypes[column]) or
                    'float' in str(data.dtypes[column])):
                agg_functions[column] = ['mean', 'sum', 'max', 'min']
            else:
                # Just pick the first value
                agg_functions[column] = ['first']

    # Resolve names into functions using AGGREGATION_FUNCTIONS map
    agg_functions = {
        col: [AGGREGATION_FUNCTIONS[name] for name in names]
        for col, names in agg_functions.items()
    }

    # Perform group-by
    data = data.groupby(by=[UNIQUE_INDEX_KEY]).agg(agg_functions)

    # Drop group-by column
    data.reset_index(drop=True, inplace=True)

    # Reorder columns
    # sorted() is a stable sort, so we'll keep the order of agg_functions above
    data = data[sorted(
        data.columns,
        key=lambda col: col_indices.get(col[0], 999999999)
    )]

    # Rename columns
    data.columns = [
        col[0] if col[1] == 'first' and len(agg_functions[col[0]]) <= 1
        else ' '.join(col[::-1]).strip()
        for col in data.columns
    ]

    logger.info("Aggregations completed in %.4fs", time.perf_counter() - start)
    return data


CHUNK_SIZE_ROWS = 10000


def _tree_nearest(tree, max_dist):
    def transform(df):
        # Convert to numeric numpy array
        points = pd.DataFrame({
            'x': pd.to_numeric(
                df.iloc[:, 0],
                errors='coerce',
                downcast='float',
            ),
            'y': pd.to_numeric(
                df.iloc[:, 1],
                errors='coerce',
                downcast='float',
            ),
        }).values
        # Run input through tree
        dist, indices = tree.query(points, return_distance=True)
        indices = indices.reshape((-1,))
        dist = dist.reshape((-1,))

        # Build array of transformed coordinates
        coords = tree.get_arrays()[0]
        res = coords[indices]

        # Discard points too far
        res[dist >= max_dist] = np.nan
        return res

    return transform


KEEP_COLUMN_FIELDS = {'name', 'structural_type', 'semantic_types'}


def join(
    original_data, augment_data_path, original_metadata, augment_metadata,
    writer,
    left_columns, right_columns,
    how='left', columns=None,
    agg_functions=None, temporal_resolution=None,
    return_only_datamart_data=False,
):
    """
    Performs a join between original_data (pandas.DataFrame or path to CSV)
    and augment_data (pandas.DataFrame) using left_columns and right_columns.

    The result is written to the writer object.

    Returns the metadata for the result.
    """

    if isinstance(original_data, pd.DataFrame):
        pass
    elif hasattr(original_data, 'read'):
        original_data = pd.read_csv(
            original_data,
            error_bad_lines=False,
            dtype=str,
        )
    else:
        raise TypeError(
            "join() argument 1 should be a path (str) or a DataFrame, got "
            "%r" % type(original_data)
        )

    augment_data_columns = [col['name'] for col in augment_metadata['columns']]

    # only converting data types for columns involved in augmentation
    original_join_columns_idx = []
    augment_join_columns_idx = []
    augment_columns_transform = []
    for left, right in zip(left_columns, right_columns):
        if len(left) == 2 and len(right) == 2:
            # Spatial augmentation
            # Get those columns
            points = original_data.iloc[:, left]
            # De-duplicate
            points = pd.DataFrame(list(
                set(tuple(p) for p in points.values)
            ))
            # Convert to numeric numpy array
            points = pd.DataFrame({
                'x': pd.to_numeric(
                    points.iloc[:, 0],
                    errors='coerce',
                    downcast='float',
                ),
                'y': pd.to_numeric(
                    points.iloc[:, 1],
                    errors='coerce',
                    downcast='float',
                ),
            }).values
            # Build KDTree
            tree = KDTree(points)
            # Compute max distance for nearest join
            max_dist = 2 * median_smallest_distance(points, tree)
            logger.info("Using nearest spatial join, max=%r", max_dist)
            # Store transformation
            augment_columns_transform.append((
                right,
                _tree_nearest(tree, max_dist),
            ))

            original_join_columns_idx.extend(left)
            augment_join_columns_idx.extend(right)
        elif len(left) > 1 or len(right) > 1:
            raise AugmentationError("Datamart currently does not support "
                                    "combination of columns for augmentation.")
        else:
            original_join_columns_idx.append(left[0])
            augment_join_columns_idx.append(right[0])

    original_data = set_data_index(
        original_data,
        original_join_columns_idx,
        original_metadata['columns'],
        drop=False,  # Keep the values of join columns from this side
    )

    # Add a column of unique indices which will be used to aggregate
    original_data[UNIQUE_INDEX_KEY] = pd.RangeIndex(len(original_data))

    logger.info("Performing join...")

    # Stream the data in
    augment_data_chunks = pd.read_csv(
        augment_data_path,
        error_bad_lines=False,
        chunksize=CHUNK_SIZE_ROWS,
    )
    try:
        first_augment_data = next(augment_data_chunks)
    except StopIteration:
        raise AugmentationError("Empty augmentation data")

    # Columns to drop
    drop_columns = None
    if columns:
        drop_columns = list(
            # Drop all the columns in augment_data
            set(augment_data_columns[c] for c in columns)
            # except
            - (
                # the requested columns
                set(columns)
                # and the join columns
                | {col[0] for col in right_columns}
            )
        )

    # Defer temporal alignment until reading the first block from companion
    # (and converting it to the right data types!)
    update_idx = None
    original_data_res = None

    # Streaming join
    start = time.perf_counter()
    join_ = []
    # Iterate over chunks of augment data
    for augment_data in itertools.chain(
            [first_augment_data], augment_data_chunks
    ):
        # Run transforms
        for cols, transform in augment_columns_transform:
            augment_data.iloc[:, cols] = transform(augment_data.iloc[:, cols])

        # Convert data types
        augment_data = set_data_index(
            augment_data,
            augment_join_columns_idx,
            augment_metadata['columns'],
            drop=True,  # Drop the join columns on that side (avoid duplicates)
        )

        if update_idx is None:
            # Guess temporal resolutions (on first chunk)
            update_idx = match_temporal_resolutions(
                original_data,
                augment_data,
                temporal_resolution,
            )
            original_data_res = original_data.set_index(
                update_idx(original_data.index)
            )

        # Match temporal resolutions
        augment_data.index = update_idx(augment_data.index)

        # Filter columns
        if drop_columns:
            augment_data = augment_data.drop(drop_columns, axis=1)

        # Join
        joined_chunk = original_data_res.join(
            augment_data,
            how=how,
            rsuffix='_r'
        )

        # Drop the join columns we set as index
        joined_chunk.reset_index(drop=True, inplace=True)

        join_.append(joined_chunk)

    join_ = pd.concat(join_)
    logger.info("Join completed in %.4fs", time.perf_counter() - start)

    intersection = set(original_data.columns).intersection(set(first_augment_data.columns))

    # qualities
    qualities_list = []

    if return_only_datamart_data:
        # drop unique index
        join_.drop([UNIQUE_INDEX_KEY], axis=1, inplace=True)

        # drop columns from original data
        drop_columns = list(intersection)
        drop_columns.extend(set(original_data.columns).difference(intersection))
        join_.drop(drop_columns, axis=1, inplace=True)
        if intersection:
            rename = dict()
            for column in intersection:
                rename[column + '_r'] = column
            join_.rename(columns=rename, inplace=True)

        # drop rows with all null values
        join_.dropna(axis=0, how='all', inplace=True)

    else:
        # map column names for the augmentation data
        augment_columns_map = {
            name: name + '_r' if name in intersection else name
            for name in first_augment_data.columns
        }

        # aggregations
        join_ = perform_aggregations(
            join_,
            list(original_data.columns),
            agg_functions,
            augment_columns_map,
        )

        # drop unique index
        join_.drop([UNIQUE_INDEX_KEY], axis=1, inplace=True)

        original_columns_set = set(original_data.columns)
        new_columns = [
            col for col in join_.columns if col not in original_columns_set
        ]
        qualities_list.append(dict(
            qualName='augmentation_info',
            qualValue=dict(
                new_columns=new_columns,
                removed_columns=[],
                nb_rows_before=original_data.shape[0],
                nb_rows_after=join_.shape[0],
                augmentation_type='join'
            ),
            qualValueType='dict'
        ))

    with WriteCounter(writer.open_file('w')) as fout:
        join_.to_csv(fout, index=False, line_terminator='\r\n')
        size = fout.size

    # Build a dict of information about all columns
    columns_metadata = dict()
    for column in augment_metadata['columns']:
        for name, agg in itertools.chain(
            [(column['name'], None), (column['name'] + '_r', None)],
            zip(
                itertools.repeat(column['name']),
                AGGREGATION_FUNCTIONS,
            ),
        ):
            column_metadata = {
                k: v for k, v in column.items()
                if k in KEEP_COLUMN_FIELDS
            }
            if agg is not None:
                name = agg + ' ' + name
            column_metadata['name'] = name
            if agg in {'sum', 'mean'}:
                column_metadata['structural_type'] = types.FLOAT
                column_metadata['semantic_types'] = []
            elif agg == 'count':
                column_metadata['structural_type'] = types.INTEGER
                column_metadata['semantic_types'] = []
            columns_metadata[name] = column_metadata
    for column in original_metadata['columns']:
        columns_metadata[column['name']] = column

    # Then construct column metadata by looking them up in the dict
    columns_metadata = [columns_metadata[name] for name in join_.columns]

    return {
        'columns': columns_metadata,
        'size': size,
        'qualities': qualities_list,
    }


def union(original_data, augment_data_path, original_metadata, augment_metadata,
          writer,
          left_columns, right_columns,
          return_only_datamart_data=False):
    """
    Performs a union between original_data (pandas.DataFrame or path to CSV)
    and augment_data_path (path to CSV file) using columns.

    The result is streamed to the writer object.

    Returns the metadata for the result.
    """

    if isinstance(original_data, pd.DataFrame):
        original_data = iter((original_data,))
    elif hasattr(original_data, 'read'):
        original_data = iter(pd.read_csv(
            original_data,
            error_bad_lines=False,
            dtype=str,
            chunksize=CHUNK_SIZE_ROWS,
        ))
    else:
        raise TypeError(
            "union() argument 1 should be a path (str) or a DataFrame, got "
            "%r" % type(original_data)
        )

    first_original_data = next(original_data)

    augment_data_columns = [col['name'] for col in augment_metadata['columns']]

    logger.info(
        "Performing union, original_data: %r, augment_data: %r, "
        "left_columns: %r, right_columns: %r",
        first_original_data.columns, augment_data_columns,
        left_columns, right_columns,
    )

    # Column renaming
    rename = dict()
    for left, right in zip(left_columns, right_columns):
        rename[augment_data_columns[right[0]]] = \
            first_original_data.columns[left[0]]

    # Missing columns will be created as NaN
    missing_columns = list(
        set(first_original_data.columns) - set(augment_data_columns)
    )

    # Sequential d3mIndex if needed, picking up from the last value
    # FIXME: Generated d3mIndex might collide with other splits?
    d3m_index = None
    if 'd3mIndex' in first_original_data.columns:
        d3m_index = int(first_original_data['d3mIndex'].max()) + 1

    logger.info("renaming: %r, missing_columns: %r", rename, missing_columns)

    # Streaming union
    start = time.perf_counter()
    with WriteCounter(writer.open_file('w')) as fout:
        orig_rows = 0
        # Write header
        fout.write(','.join(first_original_data.columns) + '\n')
        # Write original data
        if not return_only_datamart_data:
            for chunk in itertools.chain([first_original_data], original_data):
                chunk.to_csv(
                    fout,
                    header=False,
                    index=False,
                    line_terminator='\r\n',
                )
                orig_rows += len(chunk)
                if d3m_index is not None:
                    d3m_index = max(
                        d3m_index,
                        int(chunk['d3mIndex'].max()) + 1,
                    )

        total_rows = orig_rows

        # Iterate on chunks of augment data
        augment_data_chunks = pd.read_csv(
            augment_data_path,
            error_bad_lines=False,
            dtype=str,
            chunksize=CHUNK_SIZE_ROWS,
        )
        for augment_data in augment_data_chunks:
            # Rename columns to match
            augment_data = augment_data.rename(columns=rename)

            # Add d3mIndex if needed
            if d3m_index is not None:
                augment_data['d3mIndex'] = np.arange(
                    d3m_index,
                    d3m_index + len(augment_data),
                )
                d3m_index += len(augment_data)

            # Add empty column for the missing ones
            for name in missing_columns:
                augment_data[name] = np.nan

            # Reorder columns
            augment_data = augment_data[first_original_data.columns]

            # Add to CSV output
            augment_data.to_csv(
                fout,
                header=False,
                index=False,
                line_terminator='\r\n',
            )
            total_rows += len(augment_data)

        size = fout.size
    logger.info("Union completed in %.4fs", time.perf_counter() - start)

    return {
        'columns': [
            {k: v for k, v in col.items() if k in KEEP_COLUMN_FIELDS}
            for col in original_metadata['columns']
        ],
        'size': size,
        'qualities': [dict(
            qualName='augmentation_info',
            qualValue=dict(
                new_columns=[],
                removed_columns=[],
                nb_rows_before=orig_rows,
                nb_rows_after=total_rows,
                augmentation_type='union'
            ),
            qualValueType='dict'
        )],
    }
