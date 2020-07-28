import logging
import time
import uuid

from datamart_augmentation import AugmentationError, join, union


logger = logging.getLogger(__name__)


def augment(data, newdata, metadata, task, writer,
            columns=None, return_only_datamart_data=False):
    """
    Augments original data based on the task.

    :param data: the data to be augmented, as binary file object.
    :param newdata: the path to the CSV file to augment with.
    :param metadata: the metadata of the data to be augmented.
    :param task: the augmentation task.
    :param writer: Writer on which to save the files.
    :param columns: a list of column indices from newdata that will be added to data
    :param return_only_datamart_data: only returns the portion of newdata that matches
      well with data.
    """

    if 'id' not in task:
        raise AugmentationError("Dataset id for the augmentation task not provided")

    # TODO: add support for combining multiple columns before an augmentation
    #   e.g.: [['street number', 'street', 'city']] and [['address']]
    #   currently, Datamart does not support such cases
    #   this means that spatial joins (with GPS) are not supported for now

    # Perform augmentation
    start = time.perf_counter()
    if task['augmentation']['type'] == 'join':
        output_metadata = join(
            data,
            newdata,
            metadata,
            task['metadata'],
            writer,
            task['augmentation']['left_columns'],
            task['augmentation']['right_columns'],
            columns=columns,
            agg_functions=task['augmentation'].get('agg_functions'),
            temporal_resolution=task['augmentation'].get('temporal_resolution'),  # look
            return_only_datamart_data=return_only_datamart_data,
        )
    elif task['augmentation']['type'] == 'union':
        output_metadata = union(
            data,
            newdata,
            metadata,
            task['metadata'],
            writer,
            task['augmentation']['left_columns'],
            task['augmentation']['right_columns'],
            return_only_datamart_data=return_only_datamart_data,
        )
    else:
        raise AugmentationError("Augmentation task not provided")
    logger.info("Total augmentation: %.4fs", time.perf_counter() - start)

    # Write out the metadata
    writer.set_metadata(uuid.uuid4().hex, output_metadata)
    return writer.finish()
