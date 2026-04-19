  function s = uifont(f)
% function s = uifont(f)
% First the current default uicontrol font is saved in character array s.
% Then the defaults are changed to the values in character array f.
% If no input argument or if f == 0, then the default uicontrol font
% will be set to: MS sans serif, 8 point bold

props = strcat({'defaultuicontrolfont'},...
               {'size' 'name' 'weight' 'angle' 'units'});
s = get(0,props);                             % Get old default properties
s{1} = num2str(s{1});  s = char(s);           % Convert to character array
if nargin & f  f = cellstr(f)';  f{1} = str2num(f{1}); % Convert to cell array
else f={8,'ms sans serif','bold','normal','points'}; % No input. Use defaults
end;
set(0,props,f);
