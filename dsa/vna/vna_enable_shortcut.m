function vna_enable_shortcut()
%VNA_ENABLE_SHORTCUT Switch legacy vna.p to vna_core.p so vna.m wrapper is active.

baseDir = '';
cands = {};
try
    cands{end+1} = fileparts(mfilename('fullpath')); %#ok<AGROW>
catch
end
try
    cands{end+1} = fileparts(which('vna_enable_shortcut')); %#ok<AGROW>
catch
end
try
    cands{end+1} = pwd; %#ok<AGROW>
catch
end
for i = 1:numel(cands)
    d = cands{i};
    if isempty(d)
        continue;
    end
    if exist(fullfile(d,'vna.p'),'file') == 2 || exist(fullfile(d,'vna_core.p'),'file') == 2
        baseDir = d;
        break;
    end
end
if isempty(baseDir)
    error('Cannot resolve VNA folder. Please cd into the vna directory first.');
end

pLegacy = fullfile(baseDir,'vna.p');
pCore   = fullfile(baseDir,'vna_core.p');

if exist(pCore,'file') == 2
    fprintf('Shortcut already enabled: %s\n', pCore);
    return;
end

if exist(pLegacy,'file') ~= 2
    error('Cannot find legacy vna.p at: %s', pLegacy);
end

[ok,msg] = movefile(pLegacy,pCore,'f');
if ~ok
    error(['Failed to enable shortcut. Close all MATLAB sessions and try again. ', msg]);
end

rehash;
fprintf('Enabled shortcut. You can now start with: vna\n');
end
