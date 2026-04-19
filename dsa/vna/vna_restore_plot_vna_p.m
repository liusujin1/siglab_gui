function vna_restore_plot_vna_p()
%VNA_RESTORE_PLOT_VNA_P Restore legacy plot_vna.p if it was disabled.

baseDir = fileparts(mfilename('fullpath'));
pFile = fullfile(baseDir, 'plot_vna.p');
disabledFile = fullfile(baseDir, 'plot_vna.p.disabled');

if exist(disabledFile, 'file') ~= 2
    fprintf('No disabled file found: %s\n', disabledFile);
    return;
end

if exist(pFile, 'file') == 2
    delete(pFile);
end

movefile(disabledFile, pFile, 'f');
rehash;
fprintf('Restored: %s\n', pFile);
end
