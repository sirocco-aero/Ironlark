// Lossless vertex indexing for Webots/WREN. Attribute bytes are compared exactly.
#pragma once
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <stdexcept>
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
// Vertices with byte-identical attributes share one index, numbered in order of first occurrence. An open-addressing
// table of vertex numbers, compared in place: no key copies, no allocation per vertex.
inline VertexIndex indexVertices(size_t count, std::initializer_list<Attribute> attributes) {
  std::vector<Attribute> used;
  size_t size = 0;
  for (const Attribute &attribute : attributes)
    if (attribute.data) {
      used.push_back(attribute);
      size += attribute.stride;
    }
  if (size > 64) throw std::invalid_argument("Vertex attributes exceed key capacity");
  VertexIndex result;
  result.source.reserve(count);
  result.remap.resize(count);
  auto hash = [&](size_t i) {
    uint64_t h = 0x9e3779b97f4a7c15ull;
    for (const Attribute &attribute : used) {
      const unsigned char *bytes = static_cast<const unsigned char *>(attribute.data) + i * attribute.stride;
      for (size_t offset = 0; offset < attribute.stride; offset += 8) {
        uint64_t word = 0;
        std::memcpy(&word, bytes + offset, attribute.stride - offset < 8 ? attribute.stride - offset : 8);
        h = (h ^ word) * 0xbf58476d1ce4e5b9ull;
        h ^= h >> 31;
      }
    }
    return h;
  };
  auto equal = [&](size_t a, size_t b) {
    for (const Attribute &attribute : used) {
      const unsigned char *bytes = static_cast<const unsigned char *>(attribute.data);
      if (std::memcmp(bytes + a * attribute.stride, bytes + b * attribute.stride, attribute.stride))
        return false;
    }
    return true;
  };
  size_t capacity = 16;
  while (capacity < 2 * count)
    capacity *= 2;
  const unsigned int empty = ~0u;
  std::vector<unsigned int> slots(capacity, empty);  // a vertex of each distinct value
  for (size_t i = 0; i < count; ++i) {
    size_t slot = hash(i) & (capacity - 1);
    while (slots[slot] != empty && !equal(slots[slot], i))
      slot = (slot + 1) & (capacity - 1);
    if (slots[slot] == empty) {
      slots[slot] = static_cast<unsigned int>(i);
      result.remap[i] = static_cast<unsigned int>(result.source.size());
      result.source.push_back(static_cast<unsigned int>(i));
    } else
      result.remap[i] = result.remap[slots[slot]];
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
