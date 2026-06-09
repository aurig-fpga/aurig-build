################################################################################
# demo.sdc
# Minimal Synopsys Design Constraints for aurig-build smoke testing
# Target: Intel Cyclone 10 LP (10CL025YU256C8G)
################################################################################

# Clock constraint: 50 MHz
create_clock -name sys_clk -period 20.000 [get_ports clk]
