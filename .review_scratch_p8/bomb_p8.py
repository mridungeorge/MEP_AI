import io, sys, time, signal
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api")); sys.path.insert(0, str(ROOT))
import ezdxf
from mep.skills_runner import firm_sheet as fs
doc = ezdxf.new("R2010")
prev = None
for lvl in range(7):
    blk = doc.blocks.new(f"B{lvl}")
    if prev is None:
        blk.add_line((0, 0), (1, 1))
    else:
        for i in range(10):
            blk.add_blockref(prev, (i, 0))
    prev = f"B{lvl}"
doc.modelspace().add_blockref(prev, (0, 0))
doc.modelspace().add_text("{{PROJECT}}", dxfattribs={"insert": (0, 0)})
s = io.StringIO(); doc.write(s); tb = s.getvalue().encode()
print("title block bytes", len(tb))
d2 = ezdxf.new("R2010"); d2.modelspace().add_line((0, 0), (1000, 1000)); s2 = io.StringIO(); d2.write(s2)
signal.alarm(60)
t = time.time()
try:
    fs.stamp(s2.getvalue().encode(), fs.FirmTemplates(title_block=tb), fs.values_for("hvac-dxf", {"mark": "M"}))
    print("stamp done in", round(time.time() - t, 1), "s")
except BaseException as ex:
    print("stamp", type(ex).__name__, "after", round(time.time() - t, 1), "s")
