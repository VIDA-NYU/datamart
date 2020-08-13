from datetime import datetime
import json
import logging
import os
import prometheus_client
import shutil
import uuid

from datamart_core.common import json2msg
from datamart_core.prom import PromMeasureRequest

from .base import BUCKETS, BaseHandler


logger = logging.getLogger(__name__)


PROM_UPLOAD = PromMeasureRequest(
    count=prometheus_client.Counter(
        'req_upload_count',
        "Upload requests",
    ),
    time=prometheus_client.Histogram(
        'req_upload_seconds',
        "Upload request time",
        buckets=BUCKETS,
    ),
)


class Upload(BaseHandler):
    @PROM_UPLOAD.async_()
    async def post(self):
        metadata = dict(
            name=self.get_body_argument('name', None),
            source='upload',
            materialize=dict(identifier='datamart.upload',
                             date=datetime.utcnow().isoformat() + 'Z'),
        )
        description = self.get_body_argument('description', None)
        if description:
            metadata['description'] = description
        for field, opts in self.application.custom_fields.items():
            value = self.get_body_argument(field, None)
            if value:
                if 'type' in opts:
                    type_ = opts['type']
                    if type_ == 'integer':
                        value = int(value)
                    elif type_ == 'float':
                        value = float(value)
                metadata[field] = value
            elif opts.get('required', False):
                return await self.send_error_json(
                    400,
                    "Missing field %s" % field,
                )

        if 'file' in self.request.files:
            file = self.request.files['file'][0]
            metadata['filename'] = file.filename
            manual_annotations = self.get_body_argument(
                'manual_annotations',
                None,
            )
            if manual_annotations:
                try:
                    manual_annotations = json.loads(manual_annotations)
                except json.JSONDecodeError:
                    return await self.send_error_json(
                        400,
                        "Invalid manual annotations",
                    )
                metadata['manual_annotations'] = manual_annotations

            dataset_id = 'datamart.upload.%s' % uuid.uuid4().hex

            # Write file to shared storage
            dataset_dir = os.path.join('/datasets', dataset_id)
            os.mkdir(dataset_dir)
            try:
                with open(os.path.join(dataset_dir, 'main.csv'), 'wb') as fp:
                    fp.write(file.body)
            except Exception:
                shutil.rmtree(dataset_dir)
                raise
        elif self.get_body_argument('address', None):
            # Check the URL
            address = self.get_body_argument('address')
            response = await self.http_client.fetch(address, raise_error=False)
            if response.code != 200:
                return await self.send_error_json(
                    400, "Invalid URL ({} {})".format(
                        response.code, response.reason,
                    ),
                )

            # Set identifier
            metadata['materialize']['identifier'] = 'datamart.url'

            # Set 'direct_url'
            metadata['materialize']['direct_url'] = address
            dataset_id = 'datamart.url.%s' % uuid.uuid4().hex
        else:
            return await self.send_error_json(400, "No file")

        # Add to alternate index
        self.application.elasticsearch.index(
            'pending',
            dict(
                status='queued',
                metadata=metadata,
                date=datetime.utcnow().isoformat(),
                source='upload',
                materialize=metadata['materialize'],
            ),
            id=dataset_id,
        )

        # Publish to the profiling queue
        await self.application.profile_exchange.publish(
            json2msg(
                dict(
                    id=dataset_id,
                    metadata=metadata,
                ),
                # Lower priority than on-demand datasets, but higher than base
                priority=1,
            ),
            '',
        )

        return await self.send_json({'id': dataset_id})
