# Ironlark

ArduPilot SITL flying in Webots R2025a over a recreation of Poly Haven's Pine Forest, drawn by a patched Webots renderer. README.md says how to run and develop it; ROADMAP.md says what comes next. The owner is Zaid (nottzaid on GitHub).

What follows are the owner's current decisions, each with its reason. When a reason doesn't fit the case at hand, or a rule stands between you and a clearly better result, say so and propose the change.

## Code

Match the existing code: its idiom, naming and comment density. An error the user can act on names the next step ("Port 5760 is occupied. Stop the other simulation first."), so the reader knows what to do.

The project targets one setup: Linux x86-64 with the pinned Webots, ArduPilot and Blender. Tooling (setup, build, the launcher) handles that setup's real failures, ones a reproduction or a trace through the code can show; checks for other platforms or for failures nobody can show are code nobody runs. Config covers what this repository uses.

Much of the existing behaviour is deliberate, with its reason in the commit that introduced it (`git log -S`). Alternative designs are welcome. One that reverses a recorded decision goes in its own commit, and the PR description names the decision, its recorded reason and why the new design is better, so the owner can judge the trade.

Generated outputs (`.cache/`, `worlds/pine_forest/`, `protos/`, `runs/`) come from code: change the code that makes them, and keep them out of git.

## Proving a change

Each check proves something different; run the ones that cover what changed. A change no check covers is shown by its effect before and after, such as `git status --ignored` for ignore rules; an edit to prose alone needs neither.

- `./ironlark check`: takeoff, hover and landing in the empty world, judged against ground truth. Proves the flight stack and the bridge.
- `./ironlark check --record`: the same, streaming the sensors to ROS 2 and recording a bag. `./ironlark check-recording RUN` then proves the LiDAR and camera against the calibration wall, and `./ironlark check-replay RUN` that the bag replays with the same messages, stamps and transforms.
- `./ironlark check --world forest`: the flight in the forest. Proves the world loads and the patched renderer runs; after a Webots patch changes, `./ironlark build-renderer` comes first.
- `.venv/bin/python -m unittest discover tests`: unit tests that compute real results on real files, as `tests/test_forest_build.py` does. Mock only what a test can't run (Docker, the network, a display); a test that only asserts which functions were called says nothing about behaviour.
- `g++ -std=c++11 -O2 tests/test_vertex_index.cpp -o .cache/test_vertex_index && .cache/test_vertex_index`: hard normals and UV seams survive vertex indexing (`native/vertex_index.hpp`), and a large mesh's attributes reconstruct byte for byte; `unittest` doesn't run it.

Performance claims come from measurements before and after, on the same views.

## The owner's decisions

Performance work never lowers asset quality: the owner would rather run the full-quality world slower. Fix the engine or pipeline instead, Webots patches included; reductions no viewer can perceive, such as sub-pixel LOD error, are fine.

The forest recreates the source scene, so what the source does comes from the .blend (node graphs, values) or a Cycles render of the source, never from our port, which is the thing being checked. `tools/render_lighting_reference.py` renders our exported geometry in Cycles under the source's lights, so it judges lighting, not the export. Past the forest's edge the land is sunlit and lived-in: a valley with fields, a river, a lake and a village, chosen over a grey sea of cloud the owner found bleak. Art for later regions, such as caves and industrial sites, is open (ROADMAP.md).

To judge whether a change made the look worse, render main and the branch at the same views. The newest `looks-good-*` tag is the look the owner last approved, and shows how far the look has moved since. Changing the look on purpose is fine; the owner approves a new one by tagging it. The tags stay where they are.

For now, Webots changes are patches in `native/patches/`, one per feature on the pinned upstream commit, rather than a fork. A patch's number is its place in the series and shifts when a patch is added or dropped, and commits before b463f90 number an older 71-patch series, so name patches by feature. `build-renderer` serves changed shaders and node files from the Webots checkout's working tree and compiles uncommitted edits, so a patch in progress can be tested before it's saved.

## Docs

Docs for people live in README.md and ROADMAP.md; fold new documentation into them rather than adding files. Write compressed: every sentence says something concrete about what the project does or will do, how, or why, with no promotional phrasing. The owner's shell is fish, so shell commands in docs work in both sh and fish.

## Git

Work goes on a branch and a PR, merged with a merge commit (`Merge <branch>: <what it achieved>`). A PR can be large, but each of its commits makes one coherent change, whatever its size, so a reader can follow the PR commit by commit. Only benign changes go straight to main: ones that can't change what the code, the build or the repository does, such as doc wording, a comment or a typo.

History others have stays as it is: nobody rewrites another person's commits or pushes to their PR branch, and main is rewritten only by the owner, on the owner's own commits. Feedback on someone's work goes in a review.

A commit subject is a sentence about the effect, optionally after the area it touches (`research: …`), never a type label such as `chore:`; it carries the measurement when there is one ("Renderer rebuilds use ccache when installed: 275 -> 10 s"). The body says why and what was measured.
