function [drive,ppath]=pathfind(s)
% Looks at Matlab path to find string s (vos vid vfg etc.)
% and report drive & path.
% Updated for modern path layouts: prefer ...\dsa\<s> when multiple matches exist.

  drive = [];
  ppath = [];

  if nargin < 1 || isempty(s)
      return;
  end

  s = lower(char(s));
  p = path;
  p = strrep(p,'/','\\');

  % Split MATLABPATH into entries.
  entries = strsplit(p,';');
  cands = {};
  candNorm = {};

  for k = 1:length(entries)
      e = strtrim(entries{k});
      if isempty(e)
          continue;
      end
      en = lower(strrep(e,'/','\\'));
      if length(en) >= length(s)+1
          tail = en(length(en)-length(s):length(en));
          if strcmp(tail, ['\\',s])
              cands{end+1} = e; %#ok<AGROW>
              candNorm{end+1} = en; %#ok<AGROW>
          end
      end
  end

  if ~isempty(cands)
      pick = 1;

      % Prefer classic dsa folder when present (avoids matching repo root ...\vna).
      tag = ['\\dsa\\',s];
      for k = 1:length(candNorm)
          if ~isempty(strfind(candNorm{k}, tag)) %#ok<STREMP>
              pick = k;
              break;
          end
      end

      % For vna specifically, prefer folder that actually has default.vna.
      if strcmp(s,'vna')
          for k = 1:length(cands)
              if exist([cands{k},'\\default.vna'],'file') == 2
                  pick = k;
                  break;
              end
          end
      end

      chosen = cands{pick};
      if length(chosen) >= 2
          drive = chosen(1:2);
          if length(chosen) >= 3
              ppath = chosen(3:length(chosen));
          else
              ppath = '';
          end
      else
          drive = [];
          ppath = chosen;
      end
      return;
  end

  % Fallback: inspect current directory.
  a = lower(strrep(pwd,'/','\\'));
  if ~isempty(strfind(a,s)) %#ok<STREMP>
      if length(a) >= 2
          drive = a(1:2);
          if length(a) >= 3
              ppath = a(3:length(a));
          else
              ppath = '';
          end
      else
          drive = [];
          ppath = a;
      end
  end
end
