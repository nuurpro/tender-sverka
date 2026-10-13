"""Trusted source-only interpreter startup, included in the parser fingerprint.

The shell gate admits a qualified source build and refuses startup ZIPs before
Python runs. The CLI cache prefix protects initial encodings source imports,
which precede this inline hook. -S suppresses site, .pth and sitecustomize. The
hook itself uses only frozen import machinery and the builtin sys module.
"""
PYTHON_FLAGS = ('-I', '-S', '-B', '-X', 'pycache_prefix=/dev/null', '-X', 'utf8=1')

# A non-Python gate must precede encodings initialization. The executable pin
# identifies an independently qualified /usr source build, not any arbitrary
# executable with the same filename. No input document participates in this gate.
STARTUP_GUARD = '''runtime_root=$1
expected=$2
shift 2
[ "${#expected}" -eq 64 ] || exit 76
case "$expected" in *[!0-9a-f]*) exit 76;; esac
resolved=$(/usr/bin/readlink -f -- "$1") || exit 76
system=$(/usr/bin/readlink -f -- /usr/bin/python3) || exit 76
[ "$resolved" = "$system" ] || exit 76
case "$resolved" in /usr/bin/python3.[0-9]|/usr/bin/python3.[0-9][0-9]) ;; *) exit 76;; esac
digest=$(/usr/bin/sha256sum -- "$resolved") || exit 76
[ "${digest%% *}" = "$expected" ] || exit 76
# Fail closed on every ZIP candidate, including dangling links. Both lib and
# lib64 are checked; the qualified source recipe itself uses /usr/lib.
for directory in /usr/lib /usr/lib64 /usr/local/lib /usr/local/lib64 /usr/bin/lib /usr/bin/lib64 "$runtime_root/lib" "$runtime_root/lib64" "$runtime_root/bin/lib" "$runtime_root/bin/lib64"; do
 for archive in "$directory"/python*.zip; do
  if [ -e "$archive" ] || [ -L "$archive" ]; then exit 76; fi
 done
done
for configuration in /usr/pyvenv.cfg /usr/bin/pyvenv.cfg "$runtime_root/bin/pyvenv.cfg"; do
 if [ -e "$configuration" ] || [ -L "$configuration" ]; then exit 76; fi
done
[ -f "$runtime_root/pyvenv.cfg" ] || exit 76
home=
while IFS= read -r line || [ -n "$line" ]; do
 case "$line" in 'home = '*) [ -z "$home" ] || exit 76; home=${line#'home = '};; esac
done < "$runtime_root/pyvenv.cfg"
[ "$home" = /usr/bin ] || exit 76
version=${resolved##*/python}
[ -f "/usr/lib/python$version/os.py" ] || exit 76
[ -d "/usr/lib/python$version/lib-dynload" ] || exit 76
# Initial encodings imports run before the inline hook. Missing source must not
# fall through to an unhashed legacy sourceless .pyc. UTF-8 mode fixes this set.
for source in "/usr/lib/python$version/encodings/__init__.py" "/usr/lib/python$version/encodings/aliases.py" "/usr/lib/python$version/encodings/utf_8.py"; do
 [ -f "$source" ] && [ -r "$source" ] || exit 76
done
exec "$@"
'''

SOURCE_ONLY_START = '''import sys
import _frozen_importlib_external as _source_import
assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
assert sys.flags.utf8_mode == 1
assert sys.pycache_prefix == '/dev/null'
def _source_only_code(self, fullname):
 filename = self.get_filename(fullname)
 return self.source_to_code(self.get_data(filename), filename)
def _reject_bytecode(self, fullname):
 raise ImportError('Sourceless bytecode is outside the frozen source policy')
_source_import.SourceFileLoader.get_code = _source_only_code
_source_import.SourcelessFileLoader.get_code = _reject_bytecode
sys.path_hooks[:] = [_source_import.FileFinder.path_hook(
 (_source_import.ExtensionFileLoader, _source_import.EXTENSION_SUFFIXES),
 (_source_import.SourceFileLoader, _source_import.SOURCE_SUFFIXES))]
sys.path_importer_cache.clear()
_stdlib = sys.base_prefix + '/lib/python%d.%d' % sys.version_info[:2]
sys.path[:] = [_stdlib, _stdlib + '/lib-dynload',
 sys.prefix + '/lib/python%d.%d/site-packages' % sys.version_info[:2]]
'''

# Preserve the command's final script/-c argument for bounded public test probes.
SOURCE_ONLY_DISPATCH = '''
_target = sys.argv[1:]
if _target[0] == '-c':
 sys.argv = ['-c', *_target[2:]]
 exec(compile(_target[1], '<isolated-command>', 'exec'), {'__name__':'__main__'})
else:
 sys.argv = _target
 with open(_target[0], 'rb') as _script:
  exec(compile(_script.read(), _target[0], 'exec'),
   {'__name__':'__main__', '__file__':_target[0]})
'''
