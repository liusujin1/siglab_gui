function createdFiles = vna_prepare_9ch_files()
%VNA_PREPARE_9CH_FILES Generate 9-channel-compatible copies for key VNA files.
%
% Creates:
%   default_9ch.vna
%   floor_stiffness_9ch.vna
%   floor_stiffness_bighammer_01_9ch.vna
%   floor_stiffness_bighammer_101_dsa_9ch.vna

baseDir = fileparts(mfilename('fullpath'));
targets = { ...
    'default.vna', ...
    'floor_stiffness.vna', ...
    'floor_stiffness_bighammer_01.vna', ...
    'floor_stiffness_bighammer_101_dsa.vna' ...
    };

createdFiles = {};
for i = 1:numel(targets)
    src = fullfile(baseDir, targets{i});
    if exist(src, 'file') ~= 2
        fprintf('Skip missing file: %s\n', src);
        continue;
    end
    [p, n, e] = fileparts(src);
    dst = fullfile(p, [n, '_9ch', e]);
    vna_make_9ch_compatible(src, dst, 9);
    createdFiles{end+1} = dst; %#ok<AGROW>
end

fprintf('\nCreated %d compatible files.\n', numel(createdFiles));
for i = 1:numel(createdFiles)
    fprintf('  %s\n', createdFiles{i});
end
end
