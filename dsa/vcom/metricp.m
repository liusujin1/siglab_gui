function [prefix,r,y] = metricp(x)
% function [mult,prefix,y] = metricp(x)
% Converts a number to a form using the standard metric prefixes.
%    x      : The value to be converted
%    prefix : The metric prefix which applies to the new value
%    r      : the ratio y/x
%    y      : The new value

m = 0;  y = x;    % m will be multiplier index, y will be new value
if x==0 prefix=''; r=1; return; end;
while y>900  y=.001*y;  m=m+1; end;
while y<.9   y=1000*y;  m=m-1; end;
r = y/x;
if abs(m) > 6   prefix = [ftoa('%5w',1/r) '-'];
else  ms =    ['Atto- ';'Femto-';'Pico- ';'Nano- ';'Micro-';'Milli-';...
      '      ';'Kilo- ';'Mega- ';'Giga- ';'Tera- ';'Peta- ';'1e18- ';];
      prefix = deblank(ms(m+7,:));
end;
% end function metricp
