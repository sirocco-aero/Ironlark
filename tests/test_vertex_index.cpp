#include "../native/vertex_index.hpp"
#include <cassert>
#include <cstring>
#include <random>

int main() {
  // Sharing a position must not merge a UV seam or a hard normal edge.
  const float positions[][3] = {{1,2,3},{1,2,3},{1,2,3},{1,2,3}};
  const float normals[][3] = {{0,0,1},{0,0,1},{0,0,1},{0,0,-1}};
  const float uv[][2] = {{0,0},{0,0},{1,0},{0,0}};
  auto index = loiter::indexVertices(4, {{positions, sizeof positions[0]},
                                        {normals, sizeof normals[0]}, {uv, sizeof uv[0]}});
  assert((index.remap == std::vector<unsigned int>{0,0,1,2}));
  assert((index.source == std::vector<unsigned int>{0,2,3}));

  // Reconstruct every attribute of a larger indexed mesh byte for byte.
  using Vertex = std::array<float, 10>;
  std::mt19937 random(42);
  std::vector<Vertex> input;
  for (int i = 0; i < 20000; ++i) {
    Vertex vertex;
    for (float &v : vertex) v = static_cast<float>(random() % 300) / 7;
    input.push_back(vertex);
    if (i % 2 == 0) input.push_back(vertex);
  }
  index = loiter::indexVertices(input.size(), {{input.data(), sizeof(Vertex)}});
  auto compact = input;
  loiter::compact(compact, index);
  for (size_t i = 0; i < input.size(); ++i)
    assert(std::memcmp(input[i].data(), compact[index.remap[i]].data(), sizeof(Vertex)) == 0);
  loiter::release(compact);
  assert(compact.capacity() == 0);
}
