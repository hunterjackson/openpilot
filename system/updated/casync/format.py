import abc
from dataclasses import dataclass
import io
import os
import shutil
import struct
from openpilot.system.updated.casync.reader import DirectoryChunkReader


# from https://github.com/systemd/casync/blob/e6817a79d89b48e1c6083fb1868a28f1afb32505/src/caformat.h#L49

CA_FORMAT_TABLE_TAIL_MARKER = 0xe75b9e112f17417
FLAGS = 0xb000000000000000

CA_HEADER_LEN = 32
CA_TABLE_HEADER_LEN = 16
CA_TABLE_ENTRY_LEN = 40
CA_TABLE_MIN_LEN = CA_TABLE_HEADER_LEN + CA_TABLE_ENTRY_LEN

CA_FORMAT_INDEX = 0x96824d9c7b129ff9
CA_FORMAT_TABLE = 0xe75b9e112f17417d
CA_FORMAT_ENTRY = 0x1396fabcea5bbb51
CA_FORMAT_GOODBYE = 0xdfd35c5e8327c403
CA_FORMAT_FILENAME = 0x6dbb6ebcb3161f0b
CA_FORMAT_PAYLOAD = 0x8b9e1d93d6dcffc9

CA_MAX_FILENAME_SIZE = 256

@dataclass
class CAFormatHeader(abc.ABC):
  size: int
  type: int

  @staticmethod
  @abc.abstractmethod
  def from_buffer(b: io.BytesIO) -> 'CAFormatHeader':
    pass


def create_header_with_type(MAGIC_TYPE) -> type[CAFormatHeader]:
  class MagicCAFormatHeader(CAFormatHeader):
    @staticmethod
    def from_buffer(b: io.BytesIO):
      # Parse table header
      length, magic = struct.unpack("<QQ", b.read(CA_TABLE_HEADER_LEN))
      if magic == CA_FORMAT_GOODBYE:
        return None
      assert magic == MAGIC_TYPE
      return MagicCAFormatHeader(length, magic)

  return MagicCAFormatHeader


CAIndexHeader = create_header_with_type(CA_FORMAT_INDEX)
CATableHeader = create_header_with_type(CA_FORMAT_TABLE)
CAEntryHeader = create_header_with_type(CA_FORMAT_ENTRY)
CAFilenameHeader = create_header_with_type(CA_FORMAT_FILENAME)
CAPayloadHeader = create_header_with_type(CA_FORMAT_PAYLOAD)


@dataclass
class CAChunk:
  sha: bytes
  offset: int
  length: int

  @staticmethod
  def from_buffer(b: io.BytesIO, last_offset: int):
    new_offset = struct.unpack("<Q", b.read(8))[0]

    sha = b.read(32)
    length = new_offset - last_offset

    return CAChunk(sha, last_offset, length)


@dataclass
class CaFormatIndex:
  header: CAIndexHeader
  flags: int
  chunk_size_min: int
  chunk_size_avg: int
  chunk_size_max: int

  @staticmethod
  def from_buffer(b: io.BytesIO):
    header = CAIndexHeader.from_buffer(b)

    return CaFormatIndex(header, *struct.unpack("<QQQQ", b.read(CA_HEADER_LEN)))


@dataclass
class CAIndex:
  chunks: list[CAChunk]

  @staticmethod
  def from_buffer(b: io.BytesIO):
    b.seek(0, os.SEEK_END)
    length = b.tell()
    b.seek(0, os.SEEK_SET)

    _ = CaFormatIndex.from_buffer(b)
    _ = CATableHeader.from_buffer(b)

    num_chunks = (length - CA_HEADER_LEN - CA_TABLE_MIN_LEN) // CA_TABLE_ENTRY_LEN

    chunks = []

    offset = 0
    for _ in range(num_chunks):
      chunk = CAChunk.from_buffer(b, offset)
      offset += chunk.length
      chunks.append(chunk)

    return CAIndex(chunks)

  @staticmethod
  def from_file(filepath):
    with open(filepath, "rb") as f:
      return CAIndex.from_buffer(f)

  def chunks(self):
    return self.chunks


@dataclass
class CAFilename:
  header: CAFilenameHeader
  filename: str

  @staticmethod
  def from_buffer(b: io.BytesIO):
    header = CAFilenameHeader.from_buffer(b)
    if header is None:
      return None

    filename = b""

    while len(filename) < CA_MAX_FILENAME_SIZE:
      c = b.read(1)
      if c == b'\x00':
        break
      filename += c

    return CAFilename(header, filename.decode("utf-8"))


@dataclass
class CAEntry:
  header: CAEntryHeader
  feature_flags: int
  mode: int
  flags: int
  uid: int
  gid: int
  mtime: int

  @staticmethod
  def from_buffer(b: io.BytesIO):
    entry = CAEntryHeader.from_buffer(b)
    return CAEntry(entry, *struct.unpack("<QQQQQQ", b.read(8*6)))


@dataclass
class CAArchive:
  entry: CAEntryHeader

  @staticmethod
  def from_buffer(b: io.BytesIO):
    entry = CAEntry.from_buffer(b)
    return CAArchive(entry)


@dataclass
class CAFile:
  filename: CAFilename
  data: bytes

  @staticmethod
  def from_bytes(b: io.BytesIO):
    filename = CAFilename.from_buffer(b)
    if filename is None:
      return None
    _ = CAArchive.from_buffer(b)
    payload = CAPayloadHeader.from_buffer(b)

    data = b.read(payload.size - 16)

    return CAFile(filename, data)


@dataclass
class CATar:
  archive: CAArchive
  files: list[CAFile]

  @staticmethod
  def from_bytes(b: io.BytesIO):
    archive = CAArchive.from_buffer(b)

    files = []
    while True:
      file = CAFile.from_bytes(b)
      if file is None:
        break
      files.append(file)


    return CATar(archive, files)

  def files(self) -> list[CAFile]:

    return CAFile()


def parse_caidx(caidx_path) -> CATar:
  caidx = CAIndex.from_file(caidx_path)

  chunk_reader = DirectoryChunkReader("/tmp/test_casync/default.castr")

  data = b"".join(chunk_reader.read(chunk) for chunk in caidx.chunks)

  tar = CATar.from_bytes(io.BytesIO(data))

  return tar


def extract_tar(tar: CATar, directory: str):
  shutil.rmtree(directory)
  os.mkdir(directory)
  for file in tar.files:
    with open(f"{directory}/{file.filename.filename}", "wb") as f:
      f.write(file.data)


if __name__ == "__main__":
  tar = parse_caidx("/tmp/test_casync/test.caidx")

  extract_tar(tar, "/tmp/test")
