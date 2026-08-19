# NX Open playbook

Use this reference when converting a mined sequence into actual NXOpen code.

1. Record the smallest possible Journal for one intended operation.
2. Confirm the target NX release and its bundled Python version.
3. Inspect the local `UGOPEN/pythonStubs` files; these are the contract for that installation.
4. Remove recorder noise such as undo marks and UI cleanup only after confirming it is not semantically required.
5. Replace recorded `FindObject` identifiers with stable shop-controlled names, attributes, PMI, or geometry queries.
6. Separate pure planning from mutations. Keep a preview path that reports selected objects and proposed parameters.
7. For headless runs, use Siemens-supported execution for that release and preserve Teamcenter/licensing behavior.

Primary references:

- [NX Open Python Reference Guide](https://docs.sw.siemens.com/en-US/doc/209349590/PL20231101866122454.custom_api.nxopen_python_ref)
- [NX Open Programmer's Guide](https://docs.sw.siemens.com/en-US/doc/209349590/PL20220512394070742.nxopen_prog_guide/xid1124929)
- [Siemens batch execution support article](https://support.sw.siemens.com/en-US/okba/KB000181221_EN_US/Execute-an-external-NXOpen-Python-batch-script-without-run_journalexe-utility/index.html)

