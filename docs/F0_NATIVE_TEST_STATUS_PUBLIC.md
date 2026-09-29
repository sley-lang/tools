# F0 native test status for packaged tools

The selected public Sley 2.0.1 core can validate a new native `TestCase` candidate, but its production native test executor is unavailable. `tests.affected` returns `NATIVE_EXECUTOR_UNAVAILABLE`; no native test has been admitted or run through this release. `sley-tools test` checks external assertions against native Sley execution and reports core-owned native tests as pending.

Development work on the core's supervised native test route remains separate from the selected public Sley 2.0.1 profile. The Tools package includes an optional receiver probe and a canonical report parser for a future compatible native test executor. F0 still needs a released compatible core, an explicit Tools compatibility update, and a fresh clean installed-package run against that release. The package does not install a supervisor or silently substitute a development core.
