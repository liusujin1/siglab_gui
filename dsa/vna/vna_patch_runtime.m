function vna_patch_runtime()
%VNA_PATCH_RUNTIME Patch close callbacks for legacy vna.p runtime.
% Run this after opening vna with `vna`.

mainFig = findobj('type','figure','tag','vna_fig');
if isempty(mainFig)
    warning('vna_patch_runtime:NoMainFig','vna_fig not found. Open vna first.');
    return;
end

try
    set(mainFig(1),'CloseRequestFcn','vna_safe_close');
catch
end

try
    hMenus = findall(mainFig(1),'Type','uimenu');
    for k = 1:numel(hMenus)
        try
            lbl = get(hMenus(k),'Label');
            if ischar(lbl) && (~isempty(strfind(lbl,'Quit')) || ~isempty(strfind(lbl,'Exit')))
                set(hMenus(k),'Callback','vna_safe_close');
            end
        catch
        end
    end
catch
end

plotFig = findobj('type','figure','tag','vna_plot');
if ~isempty(plotFig)
    try
        set(plotFig(1),'CloseRequestFcn','vna_safe_close');
    catch
    end
end

disp('vna runtime close callbacks patched.');
end
