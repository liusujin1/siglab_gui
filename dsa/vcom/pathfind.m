function [drive,ppath]=pathfind(s)
% Looks at Matlab path to find string s (vos vid vfg etc.)
% and report drive & path
% Paul Mennen, DSP Technology
  a    = [';', path, ';'];                  % surround path with semicolons
  a = strrep(a,'/','\');                    % replace unix path delimeters
  a = lower(a); s = lower(s);               % path searches should be case insensitive
  s1 = findstr(['\',s,';'],a);              % search for specified path
  if isempty(s1)                            % here if s is not on the path
    a = pwd;                                % get current directory
    if findstr(s,a)                         % is s found in current directory?
         drive = a(1:2);                    % yes, return current directory
         ppath = a(3:length(a));
    else drive = [];  ppath = [];           % no, indicate path not found
    end;
  else s1 = s1(1);  s2 = s1;                % use first occurance
       while a(s1) ~= ';'  s1=s1-1; end;    % back up to previous semicolon
       while a(s2) ~= ';'  s2=s2+1; end;    % advance to next semicolon
       drive = a(s1+1:s1+2);                % 1st two characters are drive
       ppath = a(s1+3:s2-1);                % the rest is the path
  end;
