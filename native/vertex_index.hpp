// Lossless vertex indexing for Webots/WREN. Attribute bytes are compared exactly.
#pragma once
#include <array>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <stdexcept>
#include <unordered_map>
#include <vector>

namespace ironlark {
struct Attribute {
  const void *data;
  size_t stride;
};
struct VertexIndex {
  std::vector<unsigned int> source;
  std::vector<unsigned int> remap;
};
struct VertexKey {
  std::array<unsigned char, 64> bytes{};
  bool operator==(const VertexKey &other) const { return bytes == other.bytes; }
};
struct VertexHash {
  size_t operator()(const VertexKey &key) const {
    uint64_t hash = 14695981039346656037ull;
    for (unsigned char byte : key.bytes) {
      hash ^= byte;
      hash *= 1099511628211ull;
    }
    return static_cast<size_t>(hash);
  }
};
inline VertexIndex indexVertices(size_t count, std::initializer_list<Attribute> attributes) {
  size_t size = 0;
  for (const Attribute &attribute : attributes)
    if (attribute.data) size += attribute.stride;
  if (size > 64) throw std::invalid_argument("Vertex attributes exceed key capacity");
  VertexIndex result;
  result.source.reserve(count);
  result.remap.resize(count);
  std::unordered_map<VertexKey, unsigned int, VertexHash> unique;
  unique.reserve(count);
  for (size_t i = 0; i < count; ++i) {
    VertexKey key;
    size_t offset = 0;
    for (const Attribute &attribute : attributes) {
      if (!attribute.data) continue;
      const auto *bytes = static_cast<const unsigned char *>(attribute.data);
      std::memcpy(key.bytes.data() + offset, bytes + i * attribute.stride, attribute.stride);
      offset += attribute.stride;
    }
    auto inserted = unique.emplace(key, static_cast<unsigned int>(result.source.size()));
    result.remap[i] = inserted.first->second;
    if (inserted.second) result.source.push_back(static_cast<unsigned int>(i));
  }
  return result;
}
template<typename T> void compact(std::vector<T> &values, const VertexIndex &index) {
  if (values.empty()) return;
  size_t target = 0;
  for (unsigned int source : index.source) values[target++] = values[source];
  values.resize(target);
}
template<typename T> void release(std::vector<T> &values) {
  std::vector<T>().swap(values);
}
}  // namespace ironlark
