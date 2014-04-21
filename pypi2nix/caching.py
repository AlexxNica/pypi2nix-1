import os

try:
    import cPickle as pickle
except ImportError:
    import pickle as pickle  # noqa


class hashabledict(dict):
    def __hash__(self):
        return hash(tuple(sorted(self.items())))


class PersistentCache(object):
    def __init__(self, cache_file):
        """Creates a new persistent cache, retrieving/storing cached key-value
        pairs from/to the given filename.
        """
        self._cache_file = cache_file
        self._cache = None

    @property
    def cache(self):
        """The dictionary that is the actual in-memory cache.  This property
        lazily loads the cache from disk.
        """
        if self._cache is None:
            self.read_cache()
        return self._cache

    def read_cache(self):
        """Reads the cached contents into memory."""
        if os.path.exists(self._cache_file):
            with open(self._cache_file, 'r') as f:
                self._cache = pickle.load(f)
        else:
            # Create a new, empty cache otherwise (store a __format__ field
            # that can be used to version the file, should we need to make
            # changes to its internals)
            self._cache = {'__format__': 1}

    def write_cache(self):
        """Writes (pickles) the cache to disk."""
        with open(self._cache_file, 'w') as f:
            pickle.dump(self._cache, f)

    def empty_cache(self):
        self._cache = None
        if os.path.exists(self._cache_file):
            os.remove(self._cache_file)

    def __contains__(self, item):
        return hash(item) in self.cache

    def __getitem__(self, key):
        return self.cache[hash(key)]

    def __setitem__(self, key, value):
        self.cache[hash(key)] = value
        self.write_cache()

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

