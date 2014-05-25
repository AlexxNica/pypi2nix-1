{ pkgs, python, self }:
  with pkgs.lib;

{
  "nose" = { doCheck = false; };
  lxml = { buildInputs=[pkgs.libxml2 pkgs.libxslt]; };
  pysqlite = { buildInputs=[pkgs.sqlite]; };
  pytest = { doCheck = false; buildInputs=[]; };
  setuptools = { doCheck = false; };
  httpagentparser = { doCheck = false; };
  email-reply-parser = { doCheck = false; };
  cssutils = { doCheck = false; };
  redis = { doCheck = false; };
  django-paging = { doCheck = false; };
  pynliner = { doCheck = false; };
  pylibmc = { propagatedBuildInputs = [pkgs.libmemcached pkgs.cyrus_sasl pkgs.zlib]; };
  eventlet = { doCheck = false; };
  paste = { doCheck = false; };
  oauth2 = { doCheck = false; };
  protobuf = { doCheck = false; };
  httpretty = { doCheck = false; };
  cassandra-driver = { doCheck=false; };
  casscache = { doCheck=false; };
  werkzeug = { doCheck=false; };
  celery = { doCheck=false; };
  riak = { doCheck=false; };
  django-celery = { doCheck = false; };
  nydus = { doCheck = false; };
  webtest = { doCheck = false; };
  flask-login = { doCheck = false; };
  raven = { doCheck = false; };
  sentry = { preCheck = ''
    rm tests/sentry/buffer/redis/tests.py
    rm tests/sentry/quotas/redis/tests.py 
  ''; };
  psycopg2 = { buildInputs=[pkgs.postgresql]; };
  sure = {
    buildInputs = [
      self.by-version."mock-1.0.1"
      self.by-version."nose-1.3.0"
      self.by-version."six-1.6.1" 
    ];
  };
  cssselect = {
    doCheck = false;
    buildInputs = [ self.by-version."pytest-2.5.2" ];
  };
}
