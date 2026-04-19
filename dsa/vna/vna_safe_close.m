function vna_safe_close(varargin)
%VNA_SAFE_CLOSE Robust close handler for legacy VNA UI on modern MATLAB.
% It avoids shutdown callback chains that touch already-destroyed controls.

% Best-effort: disable callbacks first.
safe_set(findobj('type','figure','tag','vna_fig'), 'CloseRequestFcn', '');
safe_set(findobj('type','figure','tag','vna_plot'), 'CloseRequestFcn', '');

% Close plot window first, then main window.
safe_delete(findobj('type','figure','tag','vna_plot'));
safe_delete(findobj('type','figure','tag','vna_fig'));

% Try to clear VNA runtime globals/states.
try
    ls_vna('clear');
catch
end

% Release hardware ownership flags left by legacy startup path.
try
    hw_stat('free','in','vna.m');
catch
end
try
    hw_stat('free','out','vna.m');
catch
end
try
    hw_stat('free','in&out','vna.m');
catch
end
try
    hw_stat('clear');
catch
end

% Clear "vi running" bookkeeping if available.
try
    virun('clr','vna');
catch
end
end

function safe_set(h, prop, val)
if isempty(h)
    return;
end
for i = 1:numel(h)
    try
        if isgraphics(h(i))
            set(h(i), prop, val);
        end
    catch
    end
end
end

function safe_delete(h)
if isempty(h)
    return;
end
for i = 1:numel(h)
    try
        if isgraphics(h(i))
            delete(h(i));
        end
    catch
    end
end
end
