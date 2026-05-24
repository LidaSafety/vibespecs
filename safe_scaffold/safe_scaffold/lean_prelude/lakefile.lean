import Lake
open Lake DSL

package safeScaffoldSpecs where
  -- no extra deps; uses only Lean 4 stdlib

@[default_target]
lean_lib SafeScaffold

lean_lib Specs where
  -- emitted spec files land here; each is a top-level Lean module.
  globs := #[.submodules `Specs]
