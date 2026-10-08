import os
os.environ.setdefault('GSK_RENDERER', 'cairo')
from plume.app import main
raise SystemExit(main())
