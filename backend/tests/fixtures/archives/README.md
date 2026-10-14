# Public synthetic archive decoder fixtures

These fixtures are decoder test data from libarchive v3.8.5, pinned upstream
commit `dd897a78c662a2c7a003e7ec158cea7909557bee`, obtained using gstack /browse.
They contain generated test text or binary numbers, not expert files. Attribution
remains to libarchive and its contributors under upstream BSD licensing terms.

## Compressed RAR5

`libarchive-rar5-compressed.uu` is unchanged upstream:
https://github.com/libarchive/libarchive/blob/dd897a78c662a2c7a003e7ec158cea7909557bee/libarchive/test/test_read_format_rar5_compressed.rar.uu

Decoded SHA256: `68f9d7e2662cd5bc5607ba9c6bf1edbf121cf3b08011a7d8643091791750a7c8`.
Upstream test_read_format_rar5.c verifies its 1200-byte test.bin with little-endian
unsigned values max(0, k*k - 3*k + 1) for k=1..300. Standard common SKIP flag4 is
present on MAIN/END; FILE flags3 describe extra and data areas.

## Compressed RAR4 text

`libarchive-rar4-compressed-text.uu` derives from:
https://github.com/libarchive/libarchive/blob/dd897a78c662a2c7a003e7ec158cea7909557bee/libarchive/test/test_read_format_rar_compress_normal.rar.uu

Upstream decoded SHA256: `7d32e2ef511d0ade372345440838165df828197eff4d7816880dafde690cce03`.
It retains unchanged signature, MAIN header, complete testdir\test.txt header and
its unchanged 30-byte method0x33 compressed data, and END. Other members,
including the upstream link, are omitted. This member has no solid flag and
retains original CRC. It expands to exactly test text document\r\n (20 bytes).
Derived decoded SHA256: `352f86e5cec1341a5806bff7c0856f470e76c484e8048fbe5fd53bdf5eb441af`.

## Encoded Unicode RAR4

`libarchive-rar4-unicode.uu` derives from:
https://github.com/libarchive/libarchive/blob/dd897a78c662a2c7a003e7ec158cea7909557bee/libarchive/test/test_read_format_rar_unicode.rar.uu

Upstream decoded SHA256: `3dc3d2a8f3f6cbfeeb6f45b3bb09fa7b2bf4b52e10e80df29217fdeaa9dbda45`.
It retains unchanged signature, MAIN header, second file header with its complete
ANSI/encoded-Unicode name, unchanged five-byte stored payload kanji, and END.
Empty/other members are omitted. Decoded path:
表だよ/漢字長いファイル名long-filename-in-漢字.txt
Derived decoded SHA256: `3650d1262c913fe6d03a02497726b8aded0542181f64517e077a2c20c3e86bb7`.

These ordinary public fixtures qualify the selected standard rarfile/unrar
adapter and preserved original bytes. They are not expert documents or universal
codec certification. Earlier bespoke native byte-level forensic tests and the
native audit trace are retired under the user's standard-adapter contract.
