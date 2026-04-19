  function user_path = vip(arg1,arg2)
% function user_path = vip(arg1,arg2)
%
% User's Help 
% The vip (Virtual Instrument Path)  function allows you to designate a working
% directory to hold SigLab  measurement files. For instance, you may want
% all of your personal measurement setup files to be stored in c:\siglab\my_dir. 
% If the vip function has been used to set this path, the file storage dialog
% in SigLab applications will be initialized pointing to this directory. 
% 
% 
% To use vip, you must create the target working directory (e.g. by using Explorer)
% AND this directory must contain at least one file, of any type. 
% 
% Typing vip (with no input arguments) at the MATLAB prompt will open a file dialog box
% providing a graphical means to select the desired working directory.
% When a file in the working directory is selected the working directory path
% is stored in the file siglab\vcom\vi_path.mat and the current MATLAB working 
% directory is changed to it.
% 
% To automatically change to this directory each time MATLAB is started, create a 
% startup.m file as follows and store it in the siglab\vcom directory or somewhere
% on the MATLAB path. 
%
% % startup.m (comment)
%   disp('siglab\vcom\startup.m is executing.') % should reflect dir where startup.m resides
%   slwd = vip('get','path');
%   eval(['cd ',slwd]);
%   disp(['The SigLab working directory is set to: ',slwd]);
%
%
% To insure that there is only one startup.m  file on the MATLAB path, type
%       which startup.m
% in the MATLAB command window. Only one startup.m should be listed. 
%
% 
% Programmer's Help 
% If arg1 is specified BUT not equal to 'get', it is interpreted as a target 
% path to be written to the siglab\vcom\vi_path.mat file. 
% The target path is tested for validity before being stored in vi_path.mat file. 
%
% If arg1 == 'get', the function checks to see if file siglab\vcom\vi_path.mat exists
% and that the vi_path variable it contains defines an existing path.
% The path in vi_path.mat is returned in the user_path output argument.
% If the vi_path.mat file, or the path it contains, does not exist
% the failsafe path string, which is arg2, is returned in user_path. 
% This mode is currently used for error trapping. 
% 
% If arg1 =='clear' the vi_path.mat file is deleted (added per request of GLS).
%
%

% Dick Benson, DSP Technology

 user_path ='';
 if nargin ==0
    % if vi_path exists, start there
    pstr = vip('get','nfg');
    if strcmp(pstr,'nfg')
       % no valid user preferred path exists
       pstr = '*.*';
    else
       if strcmp(pstr(length(pstr)),'\')
          eval(['cd ''',pstr(1:(length(pstr)-1)),'''']);
          pstr = [pstr,'*.*'];
       else  
          eval(['cd ''',pstr,'''']);
          pstr = [pstr,'\*.*'];
       end;
       
    end;
    
    [file_n,vi_path] = uigetfile(pstr,'Set User Dir. Select a dir, then select any file, click Open',0.5,0.65);
    
    if file_n ~=0
       vi_path=vi_path(1:(length(vi_path)-1)); 
       [drv,path_n] = pathfind('vcom');    % where vi_path.mat will be stored
       if beyondv4
          eval(['save ''',drv,path_n,'\vi_path.mat'' vi_path -v4']);
       else
          eval(['save ''',drv,path_n,'\vi_path.mat'' vi_path']);
       end;
       eval(['cd ''',vi_path,'''']);
       disp(['The new user vi file directory is set to " ', vi_path,' ".']);
    else
       disp('The user canceled the path selection.');
       perror = 0;
       eval(['load vi_path'], 'perror=1;');
       if perror
          tmsg('file vi_path.mat is not in the siglab\vcom directory.',0,[],'vi_path.mat not found');
       else
          disp('The current user vi preferred directory is set to: ');
       end
    end;
    user_path = vi_path;
     
  elseif strcmp(arg1,'clear')
     % deletes vi_path.mat file from siglab\vcom directory 
     [drv,path_n] = pathfind('vcom');    % where vi_path.mat will be stored
     eval(['delete ''',drv,path_n,'\vi_path.mat''']);
     
  elseif strcmp(arg1,'get')
     if nargin ==2
        user_path = arg2;
     else
        user_path = '';
     end;
     if exist('vi_path.mat') ==2
        load vi_path
        currentdir = pwd;
      
        if length(dir(vi_path)) == 0
           s1 = ['path " ',vi_path,' "- specified in siglab\vcom\vi_path.mat does not appear to exist.'];
           s2 = 'You must create the directory before using it or use vip to select another directory.';
           tmsg([s1,s2],0,[],'Path not found.','modal');
        else
           user_path = [vi_path,'\'];
        end;
     else
        % do nothing, user_path has already been set to arg2 or '' 
     end
  else
     % a target path has been submitted
     if isstr(arg1)
        vi_path = arg1;
        if strcmp(vi_path(2:3),':\') 
           % could be a valid path definition
           if length(dir(vi_path)) == 0
               s1 = ['path " ',vi_path,' " does not appear to exist'];
               s2 = 'You must create the directory before attempting to use it.';
               s3 = 'The directrory must contain at least one file. Sorry.';
               tmsg([s1,s2,s3],0,[],'Path in vi_path.mat file not found.','modal');
               user_path = '';
           else
               [drv,path_n] = pathfind('vcom');    % where vi_path.mat MUST be stored
               eval(['save ''',drv,path_n,'\vi_path.mat'' vi_path']);
               user_path = vi_path;
           end;
        else
           s1 = 'This is not a valid path string, the form is  x:\y .';
           s2 = 'The second and third characters must be : and \ respectivly.';
           tmsg([s1,s2],0,[],'Invalid Path','modal');
        end;
     else
        disp('Function vip requires a string input to set the user vi file path.');
     end;
  end;
% end function vip
















