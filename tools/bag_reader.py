"""Open a recorded rosbag2 for reading, compressed (zstd per message, as ./ironlark records them) or not."""
import rosbag2_py


def open_bag(uri):
    storage = rosbag2_py.StorageOptions(uri=str(uri), storage_id="sqlite3")
    converter = rosbag2_py.ConverterOptions("cdr", "cdr")
    compressed = rosbag2_py.Info().read_metadata(str(uri), "sqlite3").compression_mode not in ("", "NONE", "none")
    reader = rosbag2_py.SequentialCompressionReader() if compressed else rosbag2_py.SequentialReader()
    reader.open(storage, converter)
    return reader
