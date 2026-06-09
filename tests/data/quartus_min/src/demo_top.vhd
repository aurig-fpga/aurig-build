--------------------------------------------------------------------------------
-- demo_top.vhd
-- Minimal VHDL top-level for aurig-build smoke testing
--------------------------------------------------------------------------------

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity demo_top is
  port (
    clk : in  std_logic;
    led : out std_logic
  );
end entity demo_top;

architecture rtl of demo_top is

  signal counter : unsigned(26 downto 0) := (others => '0');

begin

  -- Simple synchronous counter
  process(clk)
  begin
    if rising_edge(clk) then
      counter <= counter + 1;
    end if;
  end process;

  -- Toggle LED from high-order counter bit
  led <= counter(26);

end architecture rtl;
