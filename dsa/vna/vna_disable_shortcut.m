function vna_disable_shortcut()
%VNA_DISABLE_SHORTCUT Restore legacy vna.p name.

baseDir = fileparts(mfilename('fullpath'));
pLegacy = fullfile(baseDir,'vna.p');
pCore   = fullfile(baseDir,'vna_core.p');

if exist(pCore,'file') ~= 2
    fprintf('Shortcut already disabled.\n');
    return;
end

if exist(pLegacy,'file') == 2
    delete(pLegacy);
end

[ok,msg] = movefile(pCore,pLegacy,'f');
if ~ok
    error('Failed to disable shortcut: %s', msg);
end

rehash;
fprintf('Disabled shortcut. Legacy startup restored.\n');
end
