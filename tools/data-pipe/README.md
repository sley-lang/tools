# Sley Data Pipe

Data Pipe reads fixed-schema benchmark records from JSON Lines and emits selected comparisons plus a summary. Use it to find regressions in a bounded data set while keeping the comparison, selection, projection, and reduction rules in native Sley programs.

Three accepted programs do that work: [Benchmark Comparator](../../programs/benchmark-comparator/README.md), [Benchmark Selector](../../programs/benchmark-selector/README.md), and [Benchmark Summary Reducer](../../programs/benchmark-summary-reducer/README.md). The Python adapter reads and checks input records, preserves case IDs and order, invokes those graphs, and writes JSON Lines. It does not calculate the advertised comparison or summary values.

The [Data Pipe guide](../../docs/DATA_PIPE_V0.md) gives the exact input schema, sample file, project setup, command, output, and resource limits. The optional `sley-data-pipe` package and `sley-tools pipe` launcher are included in the RC7 install path. [Install RC7](../../docs/INSTALL_RC7.md)
