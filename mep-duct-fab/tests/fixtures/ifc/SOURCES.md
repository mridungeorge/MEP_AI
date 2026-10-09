# IFC fixtures: sources and licences

All files below are unmodified downloads from the public buildingSMART repository
<https://github.com/buildingSMART/Sample-Test-Files> (branch `main`, fetched 2026-10-07).

Licence: **Creative Commons Attribution 4.0 International (CC BY 4.0)**, "(C) buildingSMART International Ltd."
(see `LICENSE-bsi.txt`, copied from the repository's `LICENSE`; full text <https://creativecommons.org/licenses/by/4.0/legalcode.txt>).
Attribution is given here; the files are not changed.

| File | Upstream path | Schema | Originating system (header) | IfcSpace count |
|---|---|---|---|---|
| `bsi-arch-ifc4.ifc` | `IFC 4.0.2.1 (IFC 4 ADD2 TC1)/Simple-Scene/Building-Architecture.ifc` | IFC4 | Sketchup-IFC-manager 5.6.0 / SketchUp 2026 | 2 |
| `bsi-arch-ifc2x3.ifc` | `IFC 2.3.0.1 (IFC 2x3 TC1)/Simple-Scene/Building-Architecture.ifc` | IFC2X3 | Sketchup-IFC-manager 5.6.0 / SketchUp 2026 | 2 |
| `bsi-hvac-ifc4.ifc` | `IFC 4.0.2.1 (IFC 4 ADD2 TC1)/Simple-Scene/Building-Hvac.ifc` | IFC4 | Sketchup-IFC-manager 5.6.0 / SketchUp 2026 | 0 |

`expected.yaml` holds values derived by hand from the STEP text (not by the reader), with the derivation written next
to each file.

Limits of this fixture set (reported in `docs/ingest-accuracy.md`): three small files, one exporter (SketchUp), no
Revit or Archicad export. The `revit` and `archicad` profiles are therefore marked `unverified`.
Real Australian project exports (Revit, Archicad) are still needed from the pilot firm.
