import sys
from ryu.cmd.manager import main
sys.argv = ['ryu-manager', '--ofp-tcp-listen-port', '6653', 'controller.py']
main()
