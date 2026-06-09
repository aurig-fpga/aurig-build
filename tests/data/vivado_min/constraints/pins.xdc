################################################################################
# pins.xdc
# Minimal Xilinx Design Constraints for aurig-build smoke testing
# Target: Generic Artix-7 (xc7a100t-1csg324)
################################################################################

# Clock constraint: 100 MHz
create_clock -period 10.000 -name sys_clk [get_ports clk]

# Clock pin assignment (example pin for Artix-7 CSG324)
set_property PACKAGE_PIN E3 [get_ports clk]
set_property IOSTANDARD LVCMOS33 [get_ports clk]

# LED pin assignment (example pin for Artix-7 CSG324)
set_property PACKAGE_PIN H5 [get_ports led]
set_property IOSTANDARD LVCMOS33 [get_ports led]
