from base64 import b64decode
import contextlib
from io import BufferedReader
import json
import logging
import os
from urllib.parse import ParseResult, urlparse, urlunparse


logger = logging.getLogger(__name__)


def get_object_store():
    if os.environ.get('S3_KEY') and os.environ.get('GCS_CREDS'):
        raise EnvironmentError
    elif os.environ.get('S3_KEY'):
        return S3ObjectStore()
    elif os.environ.get('GCS_CREDS'):
        return GCSObjectStore()
    else:
        raise EnvironmentError


class ObjectStore(object):
    BUCKETS = (
        'datasets',
    )

    def __init__(self, fs, prefix=None):
        self.fs = fs
        self.prefix = prefix
        for bucket in self.BUCKETS:
            try:
                self.fs.mkdir(self.bucket(bucket))
            except Exception:
                pass

    def bucket(self, name):
        assert name in self.BUCKETS
        return '%s%s' % (self.prefix, name)

    def open(self, bucket, name, mode='rb'):
        full_name = '%s/%s' % (self.bucket(bucket), name)
        if 'w' in mode:
            logger.info("Opening for writing: %s", full_name)
            # Manually commit if __exit__ without error
            fp = self.fs.open(full_name, mode, autocommit=False)
            return _commit_discard_context(fp, full_name)
        else:
            logger.info("Opening for reading: %s", full_name)
            fp = BufferedReader(self.fs.open(full_name, mode), 10240000)
            logger.info("Opened for reading: %s", full_name)
            return fp

    def delete(self, bucket, name):
        self.fs.rm(
            '%s/%s' % (self.bucket(bucket), name),
            recursive=False,
        )

    def delete_prefix(self, bucket, name_prefix):
        prefix = '%s/%s' % (self.bucket(bucket), name_prefix)
        assert not any(char in prefix for char in '[]*?')
        files = self.fs.glob(prefix + '*')
        self.fs.rm(files, recursive=False)

    def clear_bucket(self, bucket):
        bucket = self.bucket(bucket)
        files = self.fs.ls(bucket, detail=False)
        for filename in files:
            self.fs.rm(filename, recursive=False)

    @staticmethod
    def _remove_bucket_prefix(name):
        idx = name.find('/', 1)
        return name[idx + 1:]

    def list_bucket_details(self, bucket):
        bucket = self.bucket(bucket)

        return [
            dict(item, name=self._remove_bucket_prefix(item['name']))
            for item in self.fs.ls(bucket, detail=True)
        ]

    def list_bucket_names(self, bucket):
        bucket = self.bucket(bucket)

        return [
            self._remove_bucket_prefix(name)
            for name in self.fs.ls(bucket, detail=False)
        ]

    def _build_client_url(self, url):
        return url

    def url(self, bucket, name):
        url = self.fs.url(self.bucket(bucket) + '/' + name)
        return self._build_client_url(url)

    def file_url(self, fileobj):
        if isinstance(fileobj, BufferedReader):
            fileobj = fileobj.raw
        return self._build_client_url(fileobj.url())

    def file_internal_url(self, fileobj):
        if isinstance(fileobj, BufferedReader):
            fileobj = fileobj.raw
        return fileobj.url()


class _ObjectStoreFileWrapper(object):
    def __init__(self, fp):
        self._fileobj = fp

    def write(self, buf):
        return self._fileobj.write(buf)

    def flush(self):
        self._fileobj.flush()

    def close(self):
        raise TypeError("Attempted to close ObjectStoreFileWrapper")

    def __enter__(self):
        raise TypeError("Attempted to enter ObjectStoreFileWrapper")

    def __exit__(self, exc_type, exc_val, exc_tb):
        raise TypeError("Attempted to exit ObjectStoreFileWrapper")

    def __iter__(self):
        raise TypeError("Attempted to iter ObjectStoreFileWrapper")


@contextlib.contextmanager
def _commit_discard_context(fp, filename):
    try:
        with fp:
            logger.info("Opened for writing: %s", filename)
            yield _ObjectStoreFileWrapper(fp)
    except BaseException:
        logger.info("Exception, discarding file %s", filename)
        fp.discard()
        raise
    else:
        logger.info("Committing file %s", filename)
        fp.commit()
        logger.info("Committed file %s", filename)


class GCSObjectStore(ObjectStore):
    def __init__(self):
        import gcsfs

        fs = gcsfs.GCSFileSystem(
            project=os.environ['GCS_PROJECT'],
            token=json.loads(b64decode(os.environ['GCS_CREDS'])),
            # Can't cache, listing changes during the container's lifetime
            use_listings_cache=False,
        )
        prefix = os.environ['GCS_BUCKET_PREFIX']

        super(GCSObjectStore, self).__init__(fs, prefix)

    def clear_bucket(self, bucket):
        bucket = self.bucket(bucket)
        files = self.fs.ls(bucket, detail=False)
        self.fs.rm(files, recursive=False)


class S3ObjectStore(ObjectStore):
    def __init__(self):
        import s3fs

        client_kwargs = {}
        if 'S3_URL' in os.environ:
            client_kwargs['endpoint_url'] = os.environ['S3_URL']
        fs = s3fs.S3FileSystem(
            key=os.environ['S3_KEY'],
            secret=os.environ['S3_SECRET'],
            client_kwargs=client_kwargs,
            # Can't cache, listing changes during the container's lifetime
            use_listings_cache=False,
        )
        prefix = os.environ['S3_BUCKET_PREFIX']
        self.client_url = urlparse(os.environ['S3_CLIENT_URL'])

        super(S3ObjectStore, self).__init__(fs, prefix)

    def clear_bucket(self, bucket):
        bucket = self.bucket(bucket)
        files = self.fs.ls(bucket, detail=False)
        self.fs.bulk_delete(files, recursive=False)

    def _build_client_url(self, url):
        parsed = urlparse(url)
        return urlunparse(ParseResult(
            self.client_url.scheme, self.client_url.netloc,
            parsed.path, parsed.params, parsed.query, parsed.fragment,
        ))
