function varargout = siglab(action,varargin)

switch action
%     case 'IOinit'
%         varargout{1} = 4;
%         varargout{2} = 1;
%         varargout{3} = 20000;
%         varargout{4} = '01\23\01 19:16 ';
%     case 'get'
%         var1 = varargin{1};
%         switch var1
%             case 'bias'
%                 varargout{1} = 1; % support ICP sensor
%             otherwise
%                 error('No specified command!')
%         end
%     case {'OutLevel','InpSet','Trigger','setwindow','Process','InpGain'}
    otherwise
        error('No specified command!')
end