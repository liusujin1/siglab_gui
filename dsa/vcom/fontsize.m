function f = fontsize()
% returns optimal fontsize for axis display
%
%  System font size     screenpix  fontsize
%  ------------------   ---------  --------
%
%  Small fonts (100%)      96         10
%  Large fonts (125%)     116          8

  f = (196 - get(0,'screenpix')) / 10;
% end function fontsize
