"""topoc: compiles the `.topo` topology DSL into AMPS XML configuration.

Pipeline (each stage a pure function; only `topoc.cli` touches the file system):

    .topo text -> dsl.parser.parse -> resolve.resolve -> amps.builder.build_configs
    (+ catalog) -> amps.layout.to_xml -> xmldoc.write_document
"""
