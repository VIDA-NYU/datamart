#!/usr/bin/env python3

"""This scripts updates the index for !115 and !127.

It adds the dataset "types" information (computed from column semantic types)
and the "attribute_keywords" field (compute from column names).
"""

import json
import os
import shutil
import sys

from datamart_profiler.core import expand_attribute_name
from datamart_profiler.profile_types import determine_dataset_type


def migrate(from_folder, to_folder):
    assert os.listdir(from_folder)
    assert not os.listdir(to_folder)

    datasets = []
    lazo = []
    for f in os.listdir(from_folder):
        if f.startswith('lazo.'):
            lazo.append(f)
        else:
            datasets.append(f)

    for i, dataset in enumerate(datasets):
        if i % 100 == 0:
            print("% 5d / %5d datasets processed" % (i, len(datasets)))

        with open(os.path.join(from_folder, dataset)) as fp:
            obj = json.load(fp)

        if 'attribute_keywords' not in obj:
            attribute_keywords = []
            for col in obj['columns']:
                attribute_keywords.append(col['name'])
                kw = list(expand_attribute_name(col['name']))
                if kw != [col['name']]:
                    attribute_keywords.extend(kw)
            obj['attribute_keywords'] = attribute_keywords

        if 'types' not in obj:
            dataset_types = set()
            for col in obj['columns']:
                type_ = determine_dataset_type(
                    col['structural_type'],
                    col['semantic_types'],
                )
                if type_:
                    dataset_types.add(type_)
            obj['types'] = sorted(dataset_types)

        with open(os.path.join(to_folder, dataset), 'w') as fp:
            json.dump(obj, fp, sort_keys=True, indent=2)

    print("Copying lazo data...")
    for f in lazo:
        shutil.copy2(
            os.path.join(from_folder, f),
            os.path.join(to_folder, f),
        )


if __name__ == '__main__':
    migrate(sys.argv[1], sys.argv[2])
