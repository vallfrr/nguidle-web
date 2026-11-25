# Agent instructions

Read [the engine vision](https://github.com/vallfrr/nguidle-rust/blob/main/docs/VISION.md) first. The authoritative French vision is
in the maintainer's local notes when available.

Work from the highest-priority issue in the current milestone. Use a topic branch and work in small steps. Run `just check` before each
significant commit. Open a pull request using the template and include
`Closes #N`, evidence and anything still unverified. Merge after the required
checks pass. Never push directly to `main`. Report the result in five lines.

The maintainer decides v1 feature scope, milestones, public-facing text,
visible behavior changes and manual platform/controller playtests. Do not
change the maintainer's vision. Do not add unapproved features or improvements and never reintroduce
a translation of the game into Rust. Record technical decisions in short ADRs.

Never write to the original game installation or commit game code, saves
or personal data. Use isolated test profiles. Interface resources follow
the project vision. See `README.md` and `ARCHITECTURE.md` and the engine's `docs/PORTING.md`
and `docs/ENGINE-API.md` for development and platform details.

Continue authorized work and verification without routine checkpoints.
Ask the maintainer when their decision, manual validation, or access is
required. Report completed work and remaining limits together.
